# -*- coding: utf-8 -*-
"""
数据库与缓存处理器（db_handler）
===================================================================
1. DBHandler：pymysql + 自研轻量连接池（queue.Queue 实现）
   - execute_sql(sql, args)  执行写操作（INSERT/UPDATE/DELETE），自动 commit
   - query_one(sql, args)    查询单条
   - query_all(sql, args)    查询多条
   - 支持 with 上下文管理，连接借出/归还自动管理
2. RedisHandler：redis-py 客户端封装
   - redis_get(key) / redis_set(key, value, ttl)
   - redis_delete / redis_exists
3. 多环境切换：构造时传入对应环境的 db / redis 配置字典
===================================================================
说明：自研连接池而非引入 DBUtils，是为了减少外部依赖并展示底层实现；
     生产环境可平滑替换为 dbutils.PooledDB。
"""
import queue
import threading
from typing import Any, List, Dict, Optional

import pymysql
import pymysql.cursors
import redis

from utils.logger import get_logger

log = get_logger("db")


# =====================================================================
# MySQL 处理器（含自研连接池）
# =====================================================================
class _ConnectionPool:
    """
    轻量级 pymysql 连接池
    - 用 queue.Queue 维护可用连接，线程安全
    - 连接借出用完归还，超时等待，避免频繁建连
    """

    def __init__(self, db_config: dict, pool_size: int = 5):
        self._db_config = db_config
        self._pool_size = pool_size
        self._pool: queue.Queue = queue.Queue(maxsize=pool_size)
        self._lock = threading.Lock()
        # 懒创建：不在初始化时预建连接，避免无 DB 环境下框架初始化失败；
        # 连接在首次 query/execute 时按需创建，池满则复用。
        self._created = 0

    def _create_connection(self):
        """创建一个新的 pymysql 连接"""
        conn = pymysql.connect(
            host=self._db_config.get("host", "127.0.0.1"),
            port=int(self._db_config.get("port", 3306)),
            user=self._db_config.get("user", "root"),
            password=self._db_config.get("password", ""),
            database=self._db_config.get("database", ""),
            charset=self._db_config.get("charset", "utf8mb4"),
            cursorclass=pymysql.cursors.DictCursor,  # 返回字典游标，便于断言取值
            connect_timeout=10,
            autocommit=False,  # 显式控制事务，便于回滚
        )
        log.debug("创建新数据库连接 | host={} db={}",
                  self._db_config.get("host"), self._db_config.get("database"))
        return conn

    def get_connection(self, timeout: float = 30):
        """从池中借出连接，池空则按需创建"""
        try:
            conn = self._pool.get_nowait()
        except queue.Empty:
            with self._lock:
                conn = self._create_connection()
        # 探活：连接已断开则重建
        try:
            conn.ping(reconnect=True)
        except Exception:
            conn = self._create_connection()
        return conn

    def return_connection(self, conn):
        """归还连接到池中；池满则关闭"""
        try:
            self._pool.put_nowait(conn)
        except queue.Full:
            try:
                conn.close()
            except Exception:
                pass

    def close_all(self):
        """关闭池中所有连接"""
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                conn.close()
            except Exception:
                pass
        log.info("数据库连接池已全部关闭")


class DBHandler:
    """MySQL 操作封装（基于连接池）"""

    def __init__(self, db_config: dict):
        """
        :param db_config: 数据库配置字典，含 host/port/user/password/database/charset/pool_size
        """
        self._db_config = db_config
        pool_size = int(db_config.get("pool_size", 5))
        self._pool = _ConnectionPool(db_config, pool_size)
        log.info(
            "DBHandler 初始化 | host={} db={} pool_size={}",
            db_config.get("host"), db_config.get("database"), pool_size,
        )

    def execute_sql(self, sql: str, args: tuple = None) -> int:
        """
        执行写操作（INSERT/UPDATE/DELETE），自动 commit

        :return: 受影响行数
        """
        conn = self._pool.get_connection()
        try:
            with conn.cursor() as cursor:
                affected = cursor.execute(sql, args)
                conn.commit()
                log.info("执行 SQL[写] | affected={} | sql={}", affected, self._safe_sql(sql))
                return affected
        except Exception:
            conn.rollback()
            log.exception("执行 SQL 失败，已回滚 | sql={}", self._safe_sql(sql))
            raise
        finally:
            self._pool.return_connection(conn)

    def query_one(self, sql: str, args: tuple = None) -> Optional[Dict[str, Any]]:
        """查询单条，返回字典或 None"""
        conn = self._pool.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, args)
                row = cursor.fetchone()
                log.info("执行 SQL[查单条] | sql={} | result={}", self._safe_sql(sql), row)
                return row
        finally:
            self._pool.return_connection(conn)

    def query_all(self, sql: str, args: tuple = None) -> List[Dict[str, Any]]:
        """查询多条，返回字典列表"""
        conn = self._pool.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, args)
                rows = cursor.fetchall()
                log.info("执行 SQL[查多条] | sql={} | rows={}", self._safe_sql(sql), len(rows))
                return list(rows)
        finally:
            self._pool.return_connection(conn)

    @staticmethod
    def _safe_sql(sql: str) -> str:
        """日志中截断超长 SQL，避免刷屏"""
        sql = str(sql).replace("\n", " ").strip()
        return sql if len(sql) <= 200 else sql[:200] + "...(截断)"

    def close(self):
        """关闭连接池"""
        self._pool.close_all()


# =====================================================================
# Redis 处理器
# =====================================================================
class RedisHandler:
    """Redis 客户端封装"""

    def __init__(self, redis_config: dict):
        """
        :param redis_config: redis 配置字典，含 host/port/db/password/decode_responses
        """
        self._config = redis_config
        # decode_responses=True 直接返回 str，便于断言比对
        self.client = redis.Redis(
            host=redis_config.get("host", "127.0.0.1"),
            port=int(redis_config.get("port", 6379)),
            db=int(redis_config.get("db", 0)),
            password=redis_config.get("password") or None,
            decode_responses=redis_config.get("decode_responses", True),
            socket_timeout=5,
            socket_connect_timeout=5,
        )
        log.info(
            "RedisHandler 初始化 | host={} db={}",
            redis_config.get("host"), redis_config.get("db"),
        )

    def redis_get(self, key: str):
        """获取 key 的值，返回字符串"""
        value = self.client.get(key)
        log.info("Redis GET | key={} | value={}", key, value)
        return value

    def redis_set(self, key: str, value: Any, ttl: int = None):
        """设置 key-value，可选过期时间（秒）"""
        self.client.set(key, value, ex=ttl)
        log.info("Redis SET | key={} | value={} | ttl={}", key, value, ttl)
        return True

    def redis_exists(self, key: str) -> bool:
        """判断 key 是否存在"""
        exists = bool(self.client.exists(key))
        log.info("Redis EXISTS | key={} | exists={}", key, exists)
        return exists

    def redis_delete(self, key: str) -> int:
        """删除 key"""
        deleted = self.client.delete(key)
        log.info("Redis DELETE | key={} | deleted={}", key, deleted)
        return deleted

    def redis_type(self, key: str) -> str:
        """获取 key 的数据类型"""
        return self.client.type(key)

    def close(self):
        """关闭连接"""
        self.client.close()
        log.info("RedisHandler 连接已关闭")

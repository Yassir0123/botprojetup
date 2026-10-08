import aiomysql
import asyncio
import time
from datetime import datetime
from typing import Optional, Union, List, Dict, Any, Tuple
from config import DB_CONFIG
import functools
import uuid
import contextvars

_transaction_cursor = contextvars.ContextVar("transaction_cursor", default=None)


def set_transaction_cursor(cursor):
    return _transaction_cursor.set(cursor)


def reset_transaction_cursor(token):
    _transaction_cursor.reset(token)


def get_transaction_cursor():
    return _transaction_cursor.get()

class ConnectionMetrics:
    """Class to track database connection metrics"""
    def __init__(self):
        self.active_connections = 0
        self.peak_connections = 0
        self.connection_errors = 0
        self.query_times = []
        self.max_query_times = 100  # Store last 100 query times
        self.last_error = None
        self.last_error_time = None
        self.total_queries = 0
        self.slow_queries = 0
        self.slow_query_threshold = 100  # ms
        self.waiting_queries = 0  # Track queries waiting for a connection
        self.max_waiting_queries = 0  # Track peak waiting queries
    
    def record_connection_acquired(self):
        self.active_connections += 1
        self.peak_connections = max(self.peak_connections, self.active_connections)
    
    def record_connection_released(self):
        self.active_connections = max(0, self.active_connections - 1)
    
    def record_query_time(self, time_ms):
        self.total_queries += 1
        if time_ms > self.slow_query_threshold:
            self.slow_queries += 1
            
        if len(self.query_times) >= self.max_query_times:
            self.query_times.pop(0)
        self.query_times.append(time_ms)
    
    def record_error(self, error):
        self.connection_errors += 1
        self.last_error = str(error)
        self.last_error_time = datetime.now()
    
    def record_waiting_query(self):
        self.waiting_queries += 1
        self.max_waiting_queries = max(self.max_waiting_queries, self.waiting_queries)
    
    def record_query_processed(self):
        self.waiting_queries = max(0, self.waiting_queries - 1)
    
    def get_avg_query_time(self):
        if not self.query_times:
            return 0
        return sum(self.query_times) / len(self.query_times)
    
    def get_metrics_report(self):
        return {
            "active_connections": self.active_connections,
            "peak_connections": self.peak_connections,
            "connection_errors": self.connection_errors,
            "avg_query_time": self.get_avg_query_time(),
            "total_queries": self.total_queries,
            "slow_queries": self.slow_queries,
            "slow_query_percentage": (self.slow_queries / self.total_queries * 100) if self.total_queries > 0 else 0,
            "last_error": self.last_error,
            "last_error_time": self.last_error_time,
            "waiting_queries": self.waiting_queries,
            "max_waiting_queries": self.max_waiting_queries
        }

# Command execution lock registry to prevent duplicate command execution
class CommandLockRegistry:
    def __init__(self):
        self.locks = {}
        self.lock_creation = asyncio.Lock()
    
    async def get_lock(self, user_id, command_name):
        """Get a lock for a specific user and command combination"""
        lock_key = f"{user_id}:{command_name}"
        
        # Use a lock to prevent race conditions when creating new locks
        async with self.lock_creation:
            if lock_key not in self.locks:
                self.locks[lock_key] = asyncio.Lock()
        
        return self.locks[lock_key]
    
    async def cleanup_old_locks(self):
        """Periodically clean up unused locks to prevent memory leaks"""
        while True:
            await asyncio.sleep(300)  # Run every 5 minutes
            async with self.lock_creation:
                if len(self.locks) > 10000:
                    keys_to_remove = [
                        key for key, lock in self.locks.items()
                        if not lock.locked()
                    ][:5000]
                    for key in keys_to_remove:
                        del self.locks[key]

# Global command lock registry
command_locks = CommandLockRegistry()

# Start the cleanup task
asyncio.create_task(command_locks.cleanup_old_locks())

class DatabaseManager:
    """Singleton class to manage database connections and operations"""
    _instance = None
    _pool_initialized = asyncio.Event()  # Event to track pool initialization
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DatabaseManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self._pool = None
        self.metrics = ConnectionMetrics()
        self._keep_alive_task = None
        self._initialized = True
        self._connection_retry_attempts = 3
        self._connection_retry_delay = 2  # seconds
        self._pool_init_lock = asyncio.Lock()  # Lock to prevent multiple initialization attempts
        self._warm_connections = []  # Keep a few warm connections ready
        self._connection_semaphore = asyncio.Semaphore(80)  # Limit concurrent connections to 80% of pool max
        self._query_queue = asyncio.Queue()  # Queue for handling query backpressure
        self._query_workers = []  # Worker tasks for processing queries
        self._checked_out_connection_ids = set()
        
        # Start the pool initialization immediately
        asyncio.create_task(self._initialize_pool())
    
    async def _initialize_pool(self):
        """Initialize the connection pool"""
        # Use a lock to prevent multiple concurrent initialization attempts
        async with self._pool_init_lock:
            if self._pool is not None and not self._pool._closed:
                self._pool_initialized.set()
                return
                
            try:
                start_time = time.time()
                print("Creating database pool...")
                
                self._pool = await aiomysql.create_pool(
                    host=DB_CONFIG['host'],
                    port=DB_CONFIG['port'],
                    user=DB_CONFIG['user'],
                    password=DB_CONFIG['password'],
                    db=DB_CONFIG['database'],
                    autocommit=True,
                    minsize=20,              # Initial idle connections - reduced to avoid too many connections
                    maxsize=100,             # Limit total active connections
                    pool_recycle=1200,       # Recycle connections every 1200 seconds
                    echo=False,              # Disable debug logging
                    connect_timeout=5,       # Reduce connection timeout for faster startup
                    loop=asyncio.get_event_loop()
                )
                
                init_time = time.time() - start_time
                print(f"Database pool created successfully in {init_time:.2f}s")
                
                # Signal that the pool is initialized
                self._pool_initialized.set()
                
                # Pre-warm some connections
                await self._prewarm_connections()
                
                # Start the keep-alive task
                if self._keep_alive_task is None or self._keep_alive_task.done():
                    self._keep_alive_task = asyncio.create_task(self._keep_pool_alive())
                
                # Start query worker tasks
                self._start_query_workers()
                    
            except Exception as e:
                self.metrics.record_error(e)
                print(f"Error creating connection pool: {e}")
                # Clear the event in case of failure
                self._pool_initialized.clear()
                
                # Retry initialization after a delay
                asyncio.create_task(self._delayed_retry_init())
    
    def _start_query_workers(self):
        """Start worker tasks to process queries from the queue"""
        self._query_workers = [worker for worker in self._query_workers if not worker.done()]
        target_workers = 5
        for _ in range(max(0, target_workers - len(self._query_workers))):
            worker = asyncio.create_task(self._query_worker())
            self._query_workers.append(worker)
    
    async def _query_worker(self):
        """Worker that processes queries from the queue"""
        while True:
            query_task = None
            try:
                # Get the next query from the queue
                query_task = await self._query_queue.get()
                
                try:
                    # Execute the query
                    await query_task()
                except Exception as e:
                    print(f"Error executing queued query: {e}")
                
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"Error in query worker: {e}")
                # Sleep briefly to avoid tight loop if there's a persistent error
                await asyncio.sleep(0.1)
            finally:
                if query_task is not None:
                    self._query_queue.task_done()
    
    async def _delayed_retry_init(self):
        """Retry pool initialization after a delay"""
        await asyncio.sleep(5)  # Wait 5 seconds before retrying
        await self._initialize_pool()
    
    async def _prewarm_connections(self):
        """Pre-warm some connections to improve first-time performance"""
        try:
            # Create a few warm connections
            warm_count = min(5, self._pool.maxsize // 4)  # Use up to 1/4 of max connections, but no more than 5
            
            print(f"Pre-warming {warm_count} database connections...")
            start_time = time.time()
            
            # Get connections in parallel
            tasks = [self._warm_single_connection() for _ in range(warm_count)]
            self._warm_connections = await asyncio.gather(*tasks)
            for _ in self._warm_connections:
                self.metrics.record_connection_released()
            
            warm_time = time.time() - start_time
            print(f"Pre-warmed {len(self._warm_connections)} connections in {warm_time:.2f}s")
            
        except Exception as e:
            print(f"Error pre-warming connections: {e}")
    
    async def _warm_single_connection(self):
        """Get and warm up a single connection"""
        conn = await self._pool.acquire()
        self.metrics.record_connection_acquired()
        
        # Execute a simple query to ensure the connection is fully established
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT 1")
            await cursor.fetchall()
        
        return conn
    
    async def _keep_pool_alive(self):
        """Keep the connection pool alive with periodic queries"""
        while True:
            try:
                if self._pool and not self._pool._closed:
                    # Execute a simple query to keep connections warm
                    await self.execute_query("SELECT 1")
                    
                    # Release and re-acquire warm connections periodically to prevent them from going stale
                    if self._warm_connections:
                        # Release one warm connection
                        conn = self._warm_connections.pop(0)
                        self._pool.release(conn)
                        
                        # Acquire a new one
                        new_conn = await self._warm_single_connection()
                        self.metrics.record_connection_released()
                        self._warm_connections.append(new_conn)
                else:
                    # If pool is closed, try to reinitialize it
                    self._pool_initialized.clear()
                    await self._initialize_pool()
            except Exception as e:
                print(f"Error in keep_pool_alive: {e}")
                
            # Wait before next check
            await asyncio.sleep(60)  # Check every minute
    
    async def get_connection(self):
        """Get a connection from the pool with semaphore control"""
        # Wait for pool to be initialized if it's not ready
        if not self._pool_initialized.is_set():
            try:
                # Wait with timeout to avoid hanging indefinitely
                await asyncio.wait_for(self._pool_initialized.wait(), timeout=10)
            except asyncio.TimeoutError:
                print("Timeout waiting for database pool initialization")
                # Try to initialize the pool again
                asyncio.create_task(self._initialize_pool())
                return None
        
        # Check if pool exists and is not closed
        if self._pool is None or self._pool._closed:
            self._pool_initialized.clear()
            asyncio.create_task(self._initialize_pool())
            return None
        
        semaphore_acquired = False
        try:
            await self._connection_semaphore.acquire()
            semaphore_acquired = True

            # Try to use a pre-warmed connection if available
            if self._warm_connections:
                conn = self._warm_connections.pop(0)
                self.metrics.record_connection_acquired()
                self._checked_out_connection_ids.add(id(conn))
                return conn
                
            # Otherwise get a new connection from the pool
            conn = await self._pool.acquire()
            self.metrics.record_connection_acquired()
            self._checked_out_connection_ids.add(id(conn))
            return conn
        except Exception as e:
            if semaphore_acquired:
                self._connection_semaphore.release()
            self.metrics.record_error(e)
            print(f"Error acquiring connection: {e}")
            return None
    
    async def release_connection(self, conn):
        """Release a connection back to the pool"""
        if not conn:
            return

        checked_out = id(conn) in self._checked_out_connection_ids
        if checked_out:
            self._checked_out_connection_ids.discard(id(conn))

        try:
            if self._pool and not self._pool._closed:
                # If we have fewer than our target warm connections, keep this one warm.
                if len(self._warm_connections) < 5 and len(self._warm_connections) < self._pool.maxsize // 4:
                    self._warm_connections.append(conn)
                    self.metrics.record_connection_released()
                else:
                    self._pool.release(conn)
                    self.metrics.record_connection_released()
        finally:
            if checked_out:
                self._connection_semaphore.release()
    
    async def execute_query(self, query: str, params: Optional[tuple] = None) -> Union[List[Dict[str, Any]], None]:
        """Execute a query and return the results"""
        conn = None
        start_time = time.time()
        
        try:
            active_cursor = get_transaction_cursor()
            if active_cursor is not None:
                await active_cursor.execute(query, params)
                result = await active_cursor.fetchall()

                query_time = (time.time() - start_time) * 1000
                self.metrics.record_query_time(query_time)

                if query_time > self.metrics.slow_query_threshold:
                    print(f"Slow query ({query_time:.2f}ms): {query[:100]}...")

                return list(result or [])

            conn = await self.get_connection()
            if not conn:
                return None
                
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(query, params)
                result = await cursor.fetchall()
                
                # Record query time in milliseconds
                query_time = (time.time() - start_time) * 1000
                self.metrics.record_query_time(query_time)
                
                # Log slow queries
                if query_time > self.metrics.slow_query_threshold:
                    print(f"Slow query ({query_time:.2f}ms): {query[:100]}...")
                
                return result
        except Exception as e:
            self.metrics.record_error(e)
            print(f"Error executing query: {e}")
            if get_transaction_cursor() is not None:
                raise
            return None
        finally:
            if conn:
                await self.release_connection(conn)
    
    async def execute_query_with_retry(self, query: str, params: Optional[tuple] = None, max_retries: int = 3) -> Union[List[Dict[str, Any]], None]:
        """Execute a query with automatic retry on failure"""
        if get_transaction_cursor() is not None:
            return await self.execute_query(query, params)

        # If this is the first query and pool isn't initialized yet, wait for it
        if not self._pool_initialized.is_set():
            try:
                # Use a shorter timeout for the first query to improve responsiveness
                await asyncio.wait_for(self._pool_initialized.wait(), timeout=5)
            except asyncio.TimeoutError:
                print("Timeout waiting for database pool initialization during query")
                # Continue anyway - the get_connection method will handle this
        
        for attempt in range(max_retries):
            try:
                result = await self.execute_query(query, params)
                if result is not None:  # None indicates an error
                    return result
                    
                # If we got None, wait and retry
                retry_delay = self._connection_retry_delay * (2 ** attempt)  # Exponential backoff
                print(f"Query attempt {attempt+1} failed. Retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
            except Exception as e:
                print(f"Retry attempt {attempt+1} failed with error: {e}")
                if attempt == max_retries - 1:  # Last attempt
                    raise  # Re-raise the exception if all retries failed
                await asyncio.sleep(self._connection_retry_delay * (2 ** attempt))
                
        return None  # All retries failed
    
    async def execute_transaction(self, queries: List[Tuple[str, Optional[tuple]]]) -> bool:
        """Execute multiple queries as a transaction"""
        conn = None
        
        try:
            conn = await self.get_connection()
            if not conn:
                return False
                
            # Disable autocommit for transaction
            await conn.begin()
            
            async with conn.cursor() as cursor:
                for query, params in queries:
                    await cursor.execute(query, params)
                
                # Commit the transaction
                await conn.commit()
                return True
        except Exception as e:
            # Rollback on error
            if conn:
                await conn.rollback()
            self.metrics.record_error(e)
            print(f"Transaction error: {e}")
            return False
        finally:
            if conn:
                await self.release_connection(conn)
    
    async def queue_query(self, query_func):
        """Add a query function to the queue for execution"""
        self.metrics.record_waiting_query()
        await self._query_queue.put(query_func)
        
    async def close(self):
        """Close the connection pool"""
        # Release all warm connections
        for conn in self._warm_connections:
            if self._pool and not self._pool._closed:
                self._pool.release(conn)
                self.metrics.record_connection_released()
        self._warm_connections = []
        
        # Cancel all query workers
        for worker in self._query_workers:
            worker.cancel()
        
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
            print("Database pool closed")
            
            # Clear the initialized event
            self._pool_initialized.clear()
            
            # Cancel the keep-alive task
            if self._keep_alive_task and not self._keep_alive_task.done():
                self._keep_alive_task.cancel()
                try:
                    await self._keep_alive_task
                except asyncio.CancelledError:
                    pass
    
    def get_metrics(self):
        """Get current database metrics"""
        metrics = self.metrics.get_metrics_report()
        
        # Add warm connection count to metrics
        metrics["warm_connections"] = len(self._warm_connections)
        
        # Add pool status
        if self._pool:
            metrics["pool_size"] = self._pool.size
            metrics["pool_free"] = self._pool.freesize
            metrics["pool_initialized"] = self._pool_initialized.is_set()
            metrics["connection_semaphore"] = self._connection_semaphore._value
            metrics["query_queue_size"] = self._query_queue.qsize()
        else:
            metrics["pool_size"] = 0
            metrics["pool_free"] = 0
            metrics["pool_initialized"] = False
            metrics["connection_semaphore"] = 0
            metrics["query_queue_size"] = 0
            
        return metrics

# Create a singleton instance
db = DatabaseManager()

# Export functions for backward compatibility
async def execute_query(query: str, params: Optional[tuple] = None) -> Union[List[Dict[str, Any]], None]:
    return await db.execute_query(query, params)

async def execute_query_with_retry(query: str, params: Optional[tuple] = None, max_retries: int = 3) -> Union[List[Dict[str, Any]], None]:
    return await db.execute_query_with_retry(query, params, max_retries)

async def execute_transaction(queries: List[Tuple[str, Optional[tuple]]]) -> bool:
    return await db.execute_transaction(queries)

async def get_connection():
    return await db.get_connection()

async def release_connection(conn):
    await db.release_connection(conn)

async def close_pool():
    await db.close()

def get_metrics():
    return db.get_metrics()

# Command execution lock decorator
def command_lock(command_name=None):
    """
    Decorator to prevent multiple executions of the same command by the same user
    
    Usage:
    @command_lock("equip_weapon")
    async def equip_weapon_command(ctx, weapon_id):
        # Command implementation
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, ctx, *args, **kwargs):
            # Get user ID from context
            user_id = ctx.author.id
            
            # Use function name if command_name not provided
            cmd_name = command_name or func.__name__
            
            # Get lock for this user and command
            lock = await command_locks.get_lock(user_id, cmd_name)
            
            # Try to acquire the lock with a timeout
            try:
                # Use a timeout to prevent deadlocks
                acquired = await asyncio.wait_for(lock.acquire(), timeout=5.0)
                
                # Generate a unique execution ID for logging
                exec_id = str(uuid.uuid4())[:8]
                print(f"[{exec_id}] Starting command {cmd_name} for user {user_id}")
                
                try:
                    # Execute the command
                    start_time = time.time()
                    result = await func(self, ctx, *args, **kwargs)
                    elapsed = time.time() - start_time
                    print(f"[{exec_id}] Completed command {cmd_name} for user {user_id} in {elapsed:.2f}s")
                    return result
                finally:
                    # Always release the lock
                    lock.release()
                    print(f"[{exec_id}] Released lock for command {cmd_name}, user {user_id}")
            
            except asyncio.TimeoutError:
                # If we couldn't acquire the lock in time, inform the user
                await ctx.send(f"You're already running this command. Please wait for it to complete.")
                print(f"Command {cmd_name} for user {user_id} rejected (already running)")
                return None
                
        return wrapper
    return decorator

# Transaction decorator with retry logic
def db_transaction(max_retries=3):
    """
    Decorator to wrap a function in a database transaction with retry logic
    
    Usage:
    @db_transaction(max_retries=3)
    async def update_user_inventory(user_id, item_id):
        # Function that needs transaction safety
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                conn = None
                try:
                    # Get a connection
                    conn = await db.get_connection()
                    if not conn:
                        if attempt == max_retries - 1:
                            raise Exception("Could not acquire database connection")
                        await asyncio.sleep(1)
                        continue
                    
                    # Begin transaction
                    await conn.begin()
                    
                    # Call the original function with the connection
                    result = await func(*args, conn=conn, **kwargs)
                    
                    # Commit the transaction
                    await conn.commit()
                    return result
                    
                except Exception as e:
                    # Rollback on error
                    if conn:
                        await conn.rollback()
                    
                    if attempt == max_retries - 1:
                        print(f"Transaction failed after {max_retries} attempts: {e}")
                        raise
                    
                    # Wait before retrying
                    await asyncio.sleep(0.5 * (2 ** attempt))  # Exponential backoff
                    
                finally:
                    # Always release the connection
                    if conn:
                        await db.release_connection(conn)
                        
        return wrapper
    return decorator

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import json
import threading
from contextlib import nullcontext


class Database:
    def __init__(self, path):
        self.path = Path(path)
        # Queue this worker's writes without 50 SQLite busy-handler retry loops.
        # SQLite still arbitrates other processes; transaction durability is unchanged.
        self.write_lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT NOT NULL,password TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS logins(token TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS pairings(code TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS student_packages(id TEXT PRIMARY KEY,owner TEXT NOT NULL,token_hash TEXT UNIQUE NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS package_devices(package_id TEXT NOT NULL,installation_hash TEXT NOT NULL,device_id TEXT NOT NULL,PRIMARY KEY(package_id,installation_hash));
            CREATE TABLE IF NOT EXISTS public_devices(owner TEXT NOT NULL,installation_hash TEXT NOT NULL,device_id TEXT NOT NULL,PRIMARY KEY(owner,installation_hash));
            CREATE TABLE IF NOT EXISTS public_registration_attempts(client TEXT PRIMARY KEY,count INTEGER NOT NULL,until REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY,owner TEXT NOT NULL,token TEXT UNIQUE,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS exams(id TEXT PRIMARY KEY,owner TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,exam_id TEXT NOT NULL,device_id TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,device_id TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,at REAL NOT NULL,actor TEXT NOT NULL,action TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS media(id TEXT PRIMARY KEY,event_id TEXT NOT NULL,device_id TEXT NOT NULL,path TEXT NOT NULL,mime TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_exam ON events(exam_id);
            CREATE INDEX IF NOT EXISTS commands_device ON commands(device_id);
            CREATE TABLE IF NOT EXISTS unlock_attempts(owner TEXT PRIMARY KEY,count INTEGER NOT NULL,until REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS teacher_faces(id TEXT PRIMARY KEY,owner TEXT NOT NULL,name TEXT NOT NULL,embedding TEXT NOT NULL,model TEXT NOT NULL,created_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS teacher_faces_owner ON teacher_faces(owner);
            CREATE TABLE IF NOT EXISTS face_challenges(id TEXT PRIMARY KEY,owner TEXT NOT NULL,device_id TEXT NOT NULL,body TEXT NOT NULL,expires REAL NOT NULL,used INTEGER NOT NULL,model TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS face_attempts(device_id TEXT PRIMARY KEY,count INTEGER NOT NULL,until REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS media_lifetime(id TEXT PRIMARY KEY,uploaded_at REAL NOT NULL,expires_at REAL NOT NULL);
            """)

    @contextmanager
    def connect(self, write=False):
        with self.write_lock if write else nullcontext():
            with self._connection(write) as c:
                yield c

    @contextmanager
    def _connection(self, write=False):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            if write:
                c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def decode(row):
    return json.loads(row["body"])

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import json


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT NOT NULL,password TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS logins(token TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS pairings(code TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY,owner TEXT NOT NULL,token TEXT UNIQUE,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS exams(id TEXT PRIMARY KEY,owner TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,exam_id TEXT NOT NULL,device_id TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,device_id TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,at REAL NOT NULL,actor TEXT NOT NULL,action TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS media(id TEXT PRIMARY KEY,event_id TEXT NOT NULL,device_id TEXT NOT NULL,path TEXT NOT NULL,mime TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_exam ON events(exam_id);
            CREATE INDEX IF NOT EXISTS commands_device ON commands(device_id);
            CREATE TABLE IF NOT EXISTS unlock_attempts(owner TEXT PRIMARY KEY,count INTEGER NOT NULL,until REAL NOT NULL);
            """)

    @contextmanager
    def connect(self, write=False):
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

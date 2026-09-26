"""Atomic pre-request quota ledger. Failed requests consume reservations too."""
from contextlib import closing
import sqlite3
import time


class BudgetExhausted(BaseException):
    """Not a provider error: must escape production retries/fallbacks."""


def connect(path):
    db=sqlite3.connect(path,timeout=30)
    db.execute('CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY, created REAL, trial TEXT, url TEXT)')
    return db


def used(path):
    with closing(connect(path)) as db:
        return db.execute('SELECT count(*) FROM requests').fetchone()[0]


def reserve(path, limit, trial, url):
    with closing(connect(path)) as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT count(*) FROM requests').fetchone()[0]>=limit:
            db.rollback()
            raise BudgetExhausted('AMAP_NETWORK_BUDGET_EXHAUSTED')
        db.execute('INSERT INTO requests(created,trial,url) VALUES(?,?,?)',(time.time(),trial,url))
        db.commit()

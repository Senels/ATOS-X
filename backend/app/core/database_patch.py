# Bunu backend/app/core/database.py içine manuel ekle:
# from sqlalchemy import event
# @event.listens_for(engine, "connect")
# def set_sqlite_pragma(dbapi_connection, connection_record):
#     cur=dbapi_connection.cursor()
#     cur.execute("PRAGMA journal_mode=WAL;")
#     cur.execute("PRAGMA busy_timeout=5000;")
#     cur.close()

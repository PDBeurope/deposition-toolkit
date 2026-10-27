"""PostgreSQL persistence boundary, called only by command orchestration.

Keep queries, ORM objects, and sessions inside this package. Commands exchange
validated values with the ledger and pass ordinary parameters/results to stages.
Importing this package never loads configuration or opens a connection.
"""

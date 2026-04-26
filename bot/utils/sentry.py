
import os
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

def init_sentry():
    """
    Initialize Sentry for error tracking.
    Set SENTRY_DSN in your Render environment variables.
    Get DSN from: https://sentry.io → New Project → Python → FastAPI
    """
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return  # Sentry is optional — bot works without it
    
    sentry_sdk.init(
        dsn=dsn,
        integrations=[
            FastApiIntegration(),
            SqlalchemyIntegration(),
        ],
        traces_sample_rate=0.1,   # 10% of requests traced (performance)
        environment=os.getenv("ENVIRONMENT", "production"),
        send_default_pii=False,   # Never send personal data to Sentry
    )
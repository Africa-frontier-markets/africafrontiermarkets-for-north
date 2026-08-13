web: uvicorn api_gateway.main:app --host 0.0.0.0 --port $PORT --workers 4
worker: python -m event_bus.worker

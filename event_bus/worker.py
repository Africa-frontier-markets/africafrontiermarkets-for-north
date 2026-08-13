"""
AFM Event Worker — consumes `afm:events` and reacts to payment/trade events.
"""

import asyncio
import os
from config.logging_config import configure_logging
from event_bus.event_schema import EventType
from event_bus.redis_consumer import EventConsumer

logger = configure_logging()

async def handle_event(data: dict) -> None:
    event_type = data.get("event_type")
    payload = data.get("payload", {})

    if event_type == EventType.PAYMENT_COMPLETED.value:
        logger.info(
            "Payment completed — ready for settlement",
            transaction_id=payload.get("transaction_id"),
            amount=payload.get("amount"),
            currency=payload.get("currency"),
        )
    elif event_type == EventType.PAYMENT_FAILED.value:
        logger.warning(
            "Payment failed",
            transaction_id=payload.get("transaction_id"),
            currency=payload.get("currency"),
        )
    elif event_type in (EventType.TRADE_EXECUTED.value, EventType.TRADE_FAILED.value):
        logger.info("Trade event received", event_type=event_type, payload=payload)
    else:
        logger.warning("Unhandled event type", event_type=event_type)

async def main() -> None:
    consumer = EventConsumer(group_name="afm_worker", consumer_name=f"worker_{os.getpid()}")
    try:
        logger.info("AFM worker starting", stream="afm:events")
        await consumer.consume("afm:events", handle_event)
    except Exception as e:
        logger.error("Worker failed", error=str(e))
    finally:
        await consumer.close()

if __name__ == "__main__":
    asyncio.run(main())

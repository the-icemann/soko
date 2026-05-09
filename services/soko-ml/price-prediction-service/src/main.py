import json
import os
import threading
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException
from confluent_kafka import Consumer, KafkaError

from .cache import get_redis_client, get_cached_prediction, set_cached_prediction
from .kafka_producer import PriceProducer
from .predictor import ModelRegistry, SUPPORTED_MARKETS, SUPPORTED_CROPS
from .schemas import (
    PredictionRequest, PredictionResponse, WeeklyPrediction,
    HealthResponse, MarketsResponse, CropsResponse,
)

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)
log = structlog.get_logger()

MODEL_DIR = os.getenv("MODEL_DIR", "/app/models")
SERVICE_NAME = os.getenv("SERVICE_NAME", "price-prediction-service")


def _run_request_consumer(
    registry: ModelRegistry, producer: PriceProducer, stop_event: threading.Event
) -> None:
    """Consume soko.price.requests and publish results to soko.price.results."""
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
    request_topic = os.getenv("KAFKA_PRICE_REQUEST_TOPIC", "soko.price.requests")
    consumer = Consumer({
        "bootstrap.servers": bootstrap,
        "group.id": "soko-ml-price-group",
        "auto.offset.reset": "latest",
        "enable.auto.commit": False,
    })
    consumer.subscribe([request_topic])
    log.info("price_request_consumer_started", topic=request_topic)

    try:
        while not stop_event.is_set():
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    log.error("consumer_error", error=str(msg.error()))
                continue
            try:
                data = json.loads(msg.value().decode("utf-8"))
                market = data.get("market", "")
                crop = data.get("crop", "")
                weeks_ahead = int(data.get("weeks_ahead", 4))
                request_id = data.get("request_id", "")

                if market in SUPPORTED_MARKETS and crop in SUPPORTED_CROPS:
                    predictions = registry.predict(market, crop, weeks_ahead)
                    result = {
                        "market": market, "crop": crop,
                        "currency": "UGX", "price_type": "wholesale",
                        "cached": False, "predictions": predictions,
                    }
                    producer.publish_prediction(result, request_id=request_id)
                else:
                    log.warning("unsupported_pair_in_request", market=market, crop=crop)

                consumer.commit(asynchronous=False)
            except Exception as exc:
                log.error("price_request_processing_error", error=str(exc))
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        consumer.commit(asynchronous=False)
        consumer.close()
        log.info("price_request_consumer_stopped")


@asynccontextmanager
async def lifespan(app: FastAPI):
    registry = ModelRegistry(MODEL_DIR)
    count = registry.load_all()
    log.info("models_loaded", count=count, model_dir=MODEL_DIR)

    producer = PriceProducer()
    redis_client = await get_redis_client()

    app.state.registry = registry
    app.state.producer = producer
    app.state.redis = redis_client

    stop_event = threading.Event()
    consumer_thread = threading.Thread(
        target=_run_request_consumer,
        args=(registry, producer, stop_event),
        daemon=True,
    )
    consumer_thread.start()

    yield

    stop_event.set()
    consumer_thread.join(timeout=10)
    producer.close()
    await redis_client.aclose()
    log.info("price_prediction_service_stopped")


app = FastAPI(title="Soko Price Prediction Service", version="1.0.0", lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        service=SERVICE_NAME,
        models_loaded=app.state.registry.loaded_count,
    )


@app.get("/markets", response_model=MarketsResponse)
async def markets():
    return MarketsResponse(markets=SUPPORTED_MARKETS)


@app.get("/crops", response_model=CropsResponse)
async def crops():
    return CropsResponse(crops=SUPPORTED_CROPS)


@app.post("/predict", response_model=PredictionResponse)
async def predict(request: PredictionRequest):
    if request.market not in SUPPORTED_MARKETS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported market: {request.market}. Choose from: {SUPPORTED_MARKETS}",
        )
    if request.crop not in SUPPORTED_CROPS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported crop: {request.crop}. Choose from: {SUPPORTED_CROPS}",
        )

    redis = app.state.redis
    registry: ModelRegistry = app.state.registry
    producer: PriceProducer = app.state.producer

    cached = await get_cached_prediction(redis, request.market, request.crop, request.weeks_ahead)
    if cached:
        return PredictionResponse(
            market=cached["market"],
            crop=cached["crop"],
            currency=cached["currency"],
            price_type=cached["price_type"],
            cached=True,
            predictions=[WeeklyPrediction(**p) for p in cached["predictions"]],
        )

    predictions_raw = registry.predict(request.market, request.crop, request.weeks_ahead)
    result = {
        "market": request.market,
        "crop": request.crop,
        "currency": "UGX",
        "price_type": "wholesale",
        "cached": False,
        "predictions": predictions_raw,
    }

    await set_cached_prediction(redis, request.market, request.crop, request.weeks_ahead, result)
    producer.publish_prediction(result)

    log.info(
        "prediction_served",
        market=request.market,
        crop=request.crop,
        weeks_ahead=request.weeks_ahead,
    )

    return PredictionResponse(
        market=result["market"],
        crop=result["crop"],
        currency=result["currency"],
        price_type=result["price_type"],
        cached=False,
        predictions=[WeeklyPrediction(**p) for p in predictions_raw],
    )

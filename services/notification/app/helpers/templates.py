from dataclasses import dataclass
from typing import Optional


@dataclass
class NotificationTemplate:
    title:       str
    body:        str
    entity_type: Optional[str] = None


def get_template(event: str, role: str, meta: dict = {}) -> Optional[NotificationTemplate]:
    order_ref  = meta.get("order_ref",  "your order")
    actor_name = meta.get("actor_name", "Someone")
    product    = meta.get("product",    "your product")
    message    = meta.get("message",    "")

    templates = {
        "order_placed": {
            "buyer": NotificationTemplate(
                title="Order placed!",
                body=f"Your order {order_ref} has been placed successfully.",
                entity_type="order",
            ),
            "farmer": NotificationTemplate(
                title="New order received",
                body=f"You have a new order {order_ref} for {product}.",
                entity_type="order",
            ),
        },
        "payment_confirmed": {
            "buyer": NotificationTemplate(
                title="Payment confirmed",
                body=f"Payment for order {order_ref} was successful.",
                entity_type="order",
            ),
            "farmer": NotificationTemplate(
                title="Payment received",
                body=f"Payment confirmed for order {order_ref}. Please prepare the order.",
                entity_type="order",
            ),
        },
        "payment_failed": {
            "buyer": NotificationTemplate(
                title="Payment failed",
                body=f"Payment for order {order_ref} did not go through. Please try again.",
                entity_type="order",
            ),
        },
        "order_dispatched": {
            "buyer": NotificationTemplate(
                title="Order on the way!",
                body=f"Your order {order_ref} has been dispatched.",
                entity_type="order",
            ),
        },
        "order_delivered": {
            "buyer": NotificationTemplate(
                title="Order delivered",
                body=f"Your order {order_ref} has been delivered. Enjoy your fresh produce!",
                entity_type="order",
            ),
        },
        "order_cancelled": {
            "buyer": NotificationTemplate(
                title="Order cancelled",
                body=f"Your order {order_ref} has been cancelled.",
                entity_type="order",
            ),
            "farmer": NotificationTemplate(
                title="Order cancelled",
                body=f"Order {order_ref} for {product} was cancelled.",
                entity_type="order",
            ),
        },
        "new_message": {
            "recipient": NotificationTemplate(
                title=f"New message from {actor_name}",
                body="You have a new message. Tap to read.",
                entity_type="message",
            ),
        },
        "new_review": {
            "farmer": NotificationTemplate(
                title="New review on your listing",
                body=f"{actor_name} left a review on {product}.",
                entity_type="listing",
            ),
        },
        "new_follower": {
            "farmer": NotificationTemplate(
                title="New follower",
                body=f"{actor_name} started following your farm.",
                entity_type="profile",
            ),
        },
        "system": {
            "user": NotificationTemplate(
                title="Soko",
                body=message or "Welcome to Soko!",
                entity_type=None,
            ),
            "farmer": NotificationTemplate(
                title="A buyer is looking for your produce!",
                body=message or "Someone is interested in what you grow but you have no active listings. Add one now!",
                entity_type="sell",
            ),
        },
    }

    event_templates = templates.get(event, {})
    return event_templates.get(role)
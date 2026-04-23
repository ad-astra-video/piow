#!/usr/bin/env python3
"""
Stripe Payment Implementation

Handles subscription management and card payments for the platform.
Refactored to use StripeClient with robust error handling, idempotency,
and structured logging following Stripe Python SDK best practices.
"""

import asyncio
import logging
import os
import uuid
from decimal import Decimal
from typing import Any, Dict, Optional, Literal, TYPE_CHECKING
from dataclasses import dataclass

from aiohttp import web

# Import supabase client
from supabase_client import supabase

# Stripe imports - separate runtime imports from type hints
# Note: Type ignore comments are used for Pylance compatibility with Stripe SDK
from stripe import (
    StripeClient,  # type: ignore
    StripeError,  # type: ignore
    CardError,  # type: ignore
    InvalidRequestError,  # type: ignore
    AuthenticationError,  # type: ignore
    APIConnectionError,  # type: ignore
    RateLimitError,  # type: ignore
    SignatureVerificationError,  # type: ignore
)

# Configure structured logging
logger = logging.getLogger(__name__)


@dataclass
class StripeConfig:
    """Configuration for Stripe client initialization."""
    api_key: str
    webhook_secret: str
    api_version: str = "2024-12-18.acacia"
    max_network_retries: int = 2


class StripePaymentService:
    """
    Service class for handling Stripe payment operations.
    
    Uses StripeClient for all API operations with proper error handling,
    idempotency keys, and structured logging.
    """

    # Subscription tier price IDs (configure in Stripe dashboard)
    PRICE_IDS: Dict[str, Optional[str]] = {
        'free': None,  # Free tier
        'starter': None,  # Set via environment
        'pro': None,  # Set via environment
        'enterprise': None,  # Set via environment
    }

    # Tier hierarchy for access control
    TIER_HIERARCHY: Dict[str, int] = {
        'free': 0,
        'starter': 1,
        'pro': 2,
        'enterprise': 3,
    }

    def __init__(self, config: Optional[StripeConfig] = None) -> None:
        """
        Initialize the Stripe payment service.

        Args:
            config: Optional StripeConfig object. If not provided,
                    configuration is loaded from environment variables.
        """
        if config is None:
            config = self._load_config_from_env()

        self._config = config
        self._client = StripeClient(  # type: ignore
            api_key=config.api_key,
            stripe_version=config.api_version,
            max_network_retries=config.max_network_retries,
        )

        # Load price IDs from environment
        self.PRICE_IDS['starter'] = os.environ.get("STRIPE_PRICE_STARTER")
        self.PRICE_IDS['pro'] = os.environ.get("STRIPE_PRICE_PRO")
        self.PRICE_IDS['enterprise'] = os.environ.get("STRIPE_PRICE_ENTERPRISE")

        logger.info("Stripe payment service initialized")

    def _load_config_from_env(self) -> StripeConfig:
        """
        Load Stripe configuration from environment variables.

        Returns:
            StripeConfig object with loaded configuration.

        Raises:
            ValueError: If required environment variables are missing.
        """
        api_key = os.environ.get("STRIPE_SECRET_KEY")
        webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")

        if not api_key:
            raise ValueError(
                "STRIPE_SECRET_KEY environment variable is required. "
                "Please configure your Stripe API key."
            )

        if not webhook_secret:
            raise ValueError(
                "STRIPE_WEBHOOK_SECRET environment variable is required. "
                "Please configure your Stripe webhook secret."
            )

        # Validate API key format (should start with sk_test_ or sk_live_)
        if not api_key.startswith(("sk_test_", "sk_live_", "rk_test_", "rk_live_")):
            logger.warning("Stripe API key may be invalid - unexpected prefix")

        api_version = os.environ.get("STRIPE_API_VERSION", "2024-12-18.acacia")
        max_retries = int(os.environ.get("STRIPE_MAX_RETRIES", "2"))

        return StripeConfig(
            api_key=api_key,
            webhook_secret=webhook_secret,
            api_version=api_version,
            max_network_retries=max_retries,
        )

    def _generate_idempotency_key(self, prefix: str = "") -> str:
        """
        Generate a unique idempotency key for API requests.

        Args:
            prefix: Optional prefix to identify the operation type.

        Returns:
            Unique idempotency key string.
        """
        unique_id = str(uuid.uuid4())
        return f"{prefix}{unique_id}" if prefix else unique_id

    def _log_operation(
        self,
        level: int,
        operation: str,
        user_id: Optional[str] = None,
        stripe_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        error: Optional[Exception] = None,
    ) -> None:
        """
        Log Stripe operations with structured data, avoiding sensitive information.

        Args:
            level: Logging level (e.g., logging.INFO, logging.ERROR).
            operation: Name of the operation being performed.
            user_id: Optional user ID (not logged directly for security).
            stripe_id: Optional Stripe resource ID (safe to log).
            details: Optional additional details (sanitized).
            error: Optional exception that occurred.
        """
        log_data: Dict[str, Any] = {
            "operation": operation,
        }

        if stripe_id:
            log_data["stripe_id"] = stripe_id

        if user_id:
            # Log only a hashed/obfuscated version of user_id for debugging
            log_data["user_id_hash"] = hash(user_id) & 0xFFFFFFFF

        if details:
            # Sanitize details - remove sensitive fields
            sanitized = {
                k: v for k, v in details.items()
                if k not in ('card', 'source', 'payment_method_details', 'email')
            }
            log_data["details"] = sanitized

        if error:
            log_data["error_type"] = type(error).__name__
            log_data["error_message"] = str(error)

        logger.log(level, "Stripe: %s", log_data)

    def _handle_stripe_error(
        self,
        error: StripeError,  # type: ignore
        operation: str,
        user_id: Optional[str] = None,
    ) -> None:
        """
        Handle and log Stripe API errors with appropriate severity.

        Args:
            error: The Stripe exception that was raised.
            operation: Name of the operation that failed.
            user_id: Optional user ID for context.
        """
        if isinstance(error, CardError):
            # Card was declined - log at INFO level (expected business logic)
            self._log_operation(
                logging.INFO,
                operation,
                user_id=user_id,
                error=error,
                details={"code": error.code, "decline_code": getattr(error, 'decline_code', None)},
            )
        elif isinstance(error, RateLimitError):
            # Rate limited - log at WARNING
            self._log_operation(
                logging.WARNING,
                operation,
                user_id=user_id,
                error=error,
                details={"should_retry": error.should_retry},
            )
        elif isinstance(error, (InvalidRequestError, AuthenticationError)):
            # Configuration or request error - log at ERROR
            self._log_operation(
                logging.ERROR,
                operation,
                user_id=user_id,
                error=error,
            )
        elif isinstance(error, APIConnectionError):
            # Network error - log at WARNING (may be transient)
            self._log_operation(
                logging.WARNING,
                operation,
                user_id=user_id,
                error=error,
                details={"should_retry": error.should_retry},
            )
        else:
            # Generic Stripe error - log at ERROR
            self._log_operation(
                logging.ERROR,
                operation,
                user_id=user_id,
                error=error,
                details={"http_status": getattr(error, 'http_status', None)},
            )

    async def create_stripe_customer(
        self,
        user_id: str,
        email: str,
        name: str,
    ) -> Any:  # Returns stripe.Customer
        """
        Create a Stripe customer for a user.

        Args:
            user_id: Supabase user ID
            email: User email
            name: User name

        Returns:
            Stripe Customer object

        Raises:
            StripeError: If customer creation fails
        """
        operation = "create_customer"

        try:
            # Check if customer already exists for this user
            existing_customers = await self._client.v1.customers.list_async(  # type: ignore
                params={"metadata": {"supabase_user_id": user_id}, "limit": 1}
            )

            customers_list = list(existing_customers)
            if customers_list:
                customer = customers_list[0]
                self._log_operation(
                    logging.INFO,
                    operation,
                    user_id=user_id,
                    stripe_id=customer.id,
                    details={"action": "reused_existing"},
                )
                return customer

            # Create new customer with idempotency key
            idempotency_key = self._generate_idempotency_key("customer_")

            customer = await self._client.v1.customers.create_async(  # type: ignore
                params={
                    "email": email,
                    "name": name,
                    "metadata": {"supabase_user_id": user_id},
                },
                options={"idempotency_key": idempotency_key},
            )

            self._log_operation(
                logging.INFO,
                operation,
                user_id=user_id,
                stripe_id=customer.id,
                details={"action": "created_new"},
            )
            return customer

        except StripeError as e:
            self._handle_stripe_error(e, operation, user_id)
            raise
        except Exception as e:
            self._log_operation(
                logging.ERROR,
                operation,
                user_id=user_id,
                error=e,
            )
            raise

    async def create_subscription(
        self,
        user_id: str,
        price_id: str,
        trial_days: int = 14,
    ) -> Any:  # Returns stripe.Subscription
        """
        Create a Stripe subscription for a user.

        Args:
            user_id: Supabase user ID
            price_id: Stripe price ID for the subscription tier
            trial_days: Number of trial days (default 14)

        Returns:
            Stripe Subscription object

        Raises:
            StripeError: If subscription creation fails
            ValueError: If user not found
        """
        operation = "create_subscription"

        try:
            # Get user info from Supabase
            user_result = supabase.table('users').select('email, full_name').eq('id', user_id).single().execute()

            if not user_result.data:
                raise ValueError(f"User not found: {user_id}")

            user_data = user_result.data
            email = user_data.get('email', '')
            name = user_data.get('full_name', '')

            # Get or create customer
            customer = await self.create_stripe_customer(user_id, email, name)

            # Create subscription with idempotency key
            idempotency_key = self._generate_idempotency_key("subscription_")

            subscription = await self._client.v1.subscriptions.create_async(  # type: ignore
                params={
                    "customer": customer.id,
                    "items": [{"price": price_id}],
                    "trial_period_days": trial_days,
                    "metadata": {"supabase_user_id": user_id},
                },
                options={"idempotency_key": idempotency_key},
            )

            self._log_operation(
                logging.INFO,
                operation,
                user_id=user_id,
                stripe_id=subscription.id,
                details={
                    "price_id": price_id,
                    "trial_days": trial_days,
                    "status": subscription.status,
                },
            )
            return subscription

        except StripeError as e:
            self._handle_stripe_error(e, operation, user_id)
            raise
        except ValueError as e:
            self._log_operation(
                logging.WARNING,
                operation,
                user_id=user_id,
                error=e,
            )
            raise
        except Exception as e:
            self._log_operation(
                logging.ERROR,
                operation,
                user_id=user_id,
                error=e,
            )
            raise

    async def cancel_subscription(
        self,
        subscription_id: str,
        user_id: Optional[str] = None,
    ) -> Any:  # Returns stripe.Subscription
        """
        Cancel a Stripe subscription.

        Args:
            subscription_id: Stripe subscription ID
            user_id: Optional user ID for logging

        Returns:
            Cancelled Subscription object

        Raises:
            StripeError: If cancellation fails
        """
        operation = "cancel_subscription"

        try:
            # Use idempotency key for safe retries
            idempotency_key = self._generate_idempotency_key("cancel_")

            subscription = await self._client.v1.subscriptions.cancel_async(  # type: ignore
                subscription_id,
                options={"idempotency_key": idempotency_key},
            )

            self._log_operation(
                logging.INFO,
                operation,
                user_id=user_id,
                stripe_id=subscription_id,
                details={"status": subscription.status},
            )
            return subscription

        except StripeError as e:
            self._handle_stripe_error(e, operation, user_id)
            raise
        except Exception as e:
            self._log_operation(
                logging.ERROR,
                operation,
                user_id=user_id,
                stripe_id=subscription_id,
                error=e,
            )
            raise

    async def update_subscription(
        self,
        subscription_id: str,
        price_id: str,
        user_id: Optional[str] = None,
    ) -> Any:  # Returns stripe.Subscription
        """
        Update a Stripe subscription to a new price tier.

        Args:
            subscription_id: Stripe subscription ID
            price_id: New Stripe price ID
            user_id: Optional user ID for logging

        Returns:
            Updated Subscription object

        Raises:
            StripeError: If update fails
        """
        operation = "update_subscription"

        try:
            # Retrieve current subscription to get item ID
            subscription = await self._client.v1.subscriptions.retrieve_async(  # type: ignore
                subscription_id
            )

            if not subscription.items or not subscription.items.data:
                raise InvalidRequestError(  # type: ignore
                    "Subscription has no items",
                    param="items",
                )

            subscription_item_id = subscription.items.data[0].id

            # Use idempotency key for safe retries
            idempotency_key = self._generate_idempotency_key("update_sub_")

            updated_subscription = await self._client.v1.subscriptions.update_async(  # type: ignore
                subscription_id,
                params={
                    "items": [{
                        "id": subscription_item_id,
                        "price": price_id,
                    }],
                    "proration_behavior": "create_prorations",
                },
                options={"idempotency_key": idempotency_key},
            )

            self._log_operation(
                logging.INFO,
                operation,
                user_id=user_id,
                stripe_id=subscription_id,
                details={
                    "old_price": subscription.items.data[0].price.id,
                    "new_price": price_id,
                },
            )
            return updated_subscription

        except StripeError as e:
            self._handle_stripe_error(e, operation, user_id)
            raise
        except Exception as e:
            self._log_operation(
                logging.ERROR,
                operation,
                user_id=user_id,
                stripe_id=subscription_id,
                error=e,
            )
            raise

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> bool:
        """
        Verify Stripe webhook signature.

        Args:
            payload: Raw request payload
            signature: Stripe signature header

        Returns:
            True if signature is valid, False otherwise
        """
        try:
            self._client.construct_event(  # type: ignore
                payload=payload,
                sig_header=signature,
                secret=self._config.webhook_secret,
            )
            return True
        except SignatureVerificationError as e:
            logger.warning("Invalid Stripe webhook signature: %s", e)
            return False
        except ValueError as e:
            logger.warning("Invalid webhook payload: %s", e)
            return False

    async def handle_stripe_webhook(
        self,
        request: web.Request,
    ) -> web.Response:
        """
        Handle incoming Stripe webhook events.

        Args:
            request: aiohttp request object

        Returns:
            Web response
        """
        payload = await request.read()
        signature = request.headers.get('Stripe-Signature')

        if not signature:
            logger.warning("Missing Stripe signature header")
            return web.json_response(
                {'error': 'Missing Stripe-Signature header'},
                status=400,
            )

        # Verify webhook signature
        try:
            event = self._client.construct_event(  # type: ignore
                payload=payload,
                sig_header=signature,
                secret=self._config.webhook_secret,
            )
        except SignatureVerificationError:
            logger.error("Invalid Stripe webhook signature")
            return web.json_response(
                {'error': 'Invalid signature'},
                status=400,
            )
        except ValueError as e:
            logger.error("Invalid webhook payload: %s", e)
            return web.json_response(
                {'error': 'Invalid payload'},
                status=400,
            )

        # Log event reception (safe to log event type and ID)
        logger.info(
            "Processing Stripe webhook: type=%s, id=%s",
            event.type,
            event.id,
        )

        # Event deduplication: check if we've already processed this event
        try:
            existing = await asyncio.to_thread(
                lambda: supabase.table('stripe_events')
                    .select('id')
                    .eq('stripe_event_id', event.id)
                    .execute()
            )
            if existing.data:
                logger.info("Duplicate webhook event ignored: %s", event.id)
                return web.json_response({'status': 'duplicate_ignored'})

            # Record event as processed
            await asyncio.to_thread(
                lambda: supabase.table('stripe_events').insert({
                    'stripe_event_id': event.id,
                    'event_type': event.type,
                }).execute()
            )
        except Exception as e:
            logger.error("Failed to deduplicate webhook event %s: %s", event.id, e)
            # Continue processing even if dedup fails — better to process twice than lose an event

        # Handle the event
        try:
            event_handlers = {
                'checkout.session.completed': self._handle_checkout_session_completed,
                'customer.subscription.created': self._handle_subscription_created,
                'customer.subscription.updated': self._handle_subscription_updated,
                'customer.subscription.deleted': self._handle_subscription_deleted,
                'customer.subscription.trial_will_end': self._handle_trial_will_end,
                'customer.subscription.past_due': self._handle_subscription_past_due,
                'invoice.created': self._handle_invoice_created,
                'invoice.payment_succeeded': self._handle_invoice_payment_succeeded,
                'invoice.payment_failed': self._handle_invoice_payment_failed,
            }

            handler = event_handlers.get(event.type)
            if handler:
                await handler(event.data.object)
            else:
                logger.info("Unhandled Stripe event type: %s", event.type)

            return web.json_response({'status': 'success'})

        except Exception as e:
            logger.error(
                "Error handling Stripe webhook event %s: %s",
                event.type,
                e,
                exc_info=True,
            )
            return web.json_response(
                {'error': 'Webhook handling failed'},
                status=500,
            )

    async def _handle_checkout_session_completed(
        self,
        session: Any,  # stripe.CheckoutSession
    ) -> None:
        """Handle successful checkout session completion."""
        try:
            customer_id = session.customer
            subscription_id = session.subscription
            user_id = session.metadata.get('supabase_user_id') if session.metadata else None

            if not user_id:
                logger.error("No supabase_user_id in checkout session metadata")
                return

            if not subscription_id:
                logger.error("No subscription in checkout session")
                return

            # Get subscription details from Stripe
            subscription = await self._client.v1.subscriptions.retrieve_async(  # type: ignore
                subscription_id
            )

            # Determine plan from price ID
            if subscription.items and subscription.items.data:
                price_id = subscription.items.data[0].price.id
                plan = self._get_tier_from_price_id(price_id)

                if not plan:
                    logger.error("Unknown price ID: %s", price_id)
                    return

                # Update subscriptions table with Stripe data
                subscription_data = {
                    'user_id': user_id,
                    'stripe_customer_id': customer_id,
                    'stripe_subscription_id': subscription_id,
                    'status': subscription.status,
                    'plan': plan,
                    'current_period_start': subscription.current_period_start,
                    'current_period_end': subscription.current_period_end,
                    'cancel_at_period_end': subscription.cancel_at_period_end or False,
                    'canceled_at': subscription.canceled_at,
                    'trial_start': subscription.trial_start,
                    'trial_end': subscription.trial_end,
                    'updated_at': 'now()',
                }

                # Upsert the subscription record
                result = supabase.table('subscriptions').upsert(
                    subscription_data,
                    on_conflict='user_id',
                ).execute()

                if not result.data:
                    logger.error("Failed to upsert subscription for user %s", user_id)
                else:
                    logger.info(
                        "Subscription completed for user %s: plan=%s",
                        user_id,
                        plan,
                    )

        except StripeError as e:
            self._handle_stripe_error(e, "handle_checkout_completed")
        except Exception as e:
            logger.error("Error handling checkout session completed: %s", e, exc_info=True)

    async def _handle_subscription_created(
        self,
        subscription: Any,  # stripe.Subscription
    ) -> None:
        """Handle new subscription creation."""
        try:
            user_id = subscription.metadata.get('supabase_user_id') if subscription.metadata else None

            if not user_id:
                logger.error("No supabase_user_id in subscription metadata")
                return

            # Update Supabase subscriptions table
            subscription_data = {
                'user_id': user_id,
                'stripe_subscription_id': subscription.id,
                'status': subscription.status,
                'current_period_start': subscription.current_period_start,
                'current_period_end': subscription.current_period_end,
                'cancel_at_period_end': subscription.cancel_at_period_end or False,
                'trial_start': subscription.trial_start,
                'trial_end': subscription.trial_end,
                'updated_at': 'now()',
            }

            result = supabase.table('subscriptions').upsert(
                subscription_data,
                on_conflict='user_id',
            ).execute()

            if not result.data:
                logger.error("Failed to upsert subscription for user %s", user_id)
            else:
                logger.info("Subscription created for user %s: %s", user_id, subscription.id)

        except Exception as e:
            logger.error("Error handling subscription created: %s", e, exc_info=True)

    async def _handle_subscription_updated(
        self,
        subscription: Any,  # stripe.Subscription
    ) -> None:
        """Handle subscription updates."""
        try:
            user_id = subscription.metadata.get('supabase_user_id') if subscription.metadata else None

            if not user_id:
                logger.error("No supabase_user_id in subscription metadata")
                return

            # Update Supabase subscriptions table
            subscription_data = {
                'status': subscription.status,
                'current_period_start': subscription.current_period_start,
                'current_period_end': subscription.current_period_end,
                'cancel_at_period_end': subscription.cancel_at_period_end or False,
                'canceled_at': subscription.canceled_at,
                'updated_at': 'now()',
            }

            result = supabase.table('subscriptions').update(
                subscription_data,
            ).eq('stripe_subscription_id', subscription.id).execute()

            if not result.data:
                logger.error("Failed to update subscription %s", subscription.id)
            else:
                logger.info("Subscription updated for user %s: %s", user_id, subscription.id)

        except Exception as e:
            logger.error("Error handling subscription updated: %s", e, exc_info=True)

    async def _handle_subscription_deleted(
        self,
        subscription: Any,  # stripe.Subscription
    ) -> None:
        """Handle subscription deletion/cancellation."""
        try:
            user_id = subscription.metadata.get('supabase_user_id') if subscription.metadata else None

            if not user_id:
                logger.error("No supabase_user_id in subscription metadata")
                return

            # Update Supabase subscriptions table
            subscription_data = {
                'status': 'canceled',
                'canceled_at': 'now()',
                'updated_at': 'now()',
            }

            result = supabase.table('subscriptions').update(
                subscription_data,
            ).eq('stripe_subscription_id', subscription.id).execute()

            if not result.data:
                logger.error("Failed to update subscription %s", subscription.id)
            else:
                logger.info("Subscription deleted for user %s: %s", user_id, subscription.id)

        except Exception as e:
            logger.error("Error handling subscription deleted: %s", e, exc_info=True)

    async def _handle_trial_will_end(
        self,
        subscription: Any,  # stripe.Subscription
    ) -> None:
        """
        Handle trial ending soon (sent ~3 days before trial ends).
        Use this to send reminder emails or in-app notifications.
        """
        try:
            user_id = subscription.metadata.get('supabase_user_id') if subscription.metadata else None

            if not user_id:
                logger.error("No supabase_user_id in subscription metadata")
                return

            trial_end = subscription.trial_end
            logger.info(
                "Trial ending soon for user %s, subscription %s (trial ends: %s)",
                user_id,
                subscription.id,
                trial_end,
            )

            # TODO: Trigger email notification or in-app notification
            # e.g., await send_email(user_id, 'trial_ending', {'trial_end': trial_end})

        except Exception as e:
            logger.error("Error handling trial will end: %s", e, exc_info=True)

    async def _handle_subscription_past_due(
        self,
        subscription: Any,  # stripe.Subscription
    ) -> None:
        """
        Handle subscription entering past_due state.
        Update local status and optionally trigger dunning/notification flow.
        """
        try:
            user_id = subscription.metadata.get('supabase_user_id') if subscription.metadata else None

            if not user_id:
                logger.error("No supabase_user_id in subscription metadata")
                return

            # Update subscription status to past_due
            result = supabase.table('subscriptions').update({
                'status': 'past_due',
                'updated_at': 'now()',
            }).eq('stripe_subscription_id', subscription.id).execute()

            if not result.data:
                logger.error("Failed to update subscription %s to past_due", subscription.id)
            else:
                logger.warning(
                    "Subscription %s for user %s is now past_due",
                    subscription.id,
                    user_id,
                )

            # TODO: Trigger dunning email notification
            # e.g., await send_email(user_id, 'payment_past_due', {})

        except Exception as e:
            logger.error("Error handling subscription past_due: %s", e, exc_info=True)

    async def _handle_invoice_created(
        self,
        invoice: Any,  # stripe.Invoice
    ) -> None:
        """
        Handle invoice creation.
        Useful for tracking billing lifecycle and analytics.
        """
        try:
            subscription_id = invoice.subscription

            if not subscription_id:
                # One-off invoice (not subscription-related)
                logger.info("One-off invoice created: %s", invoice.id)
                return

            logger.info(
                "Invoice %s created for subscription %s (amount_due: %s %s)",
                invoice.id,
                subscription_id,
                invoice.amount_due,
                invoice.currency,
            )

            # Optionally record draft invoice for analytics
            # (finalized/succeeded records go in transactions table)

        except Exception as e:
            logger.error("Error handling invoice created: %s", e, exc_info=True)

    async def _handle_invoice_payment_succeeded(
        self,
        invoice: Any,  # stripe.Invoice
    ) -> None:
        """
        Handle successful invoice payment.
        Records the transaction and resets usage counters for the new period.
        """
        try:
            subscription_id = invoice.subscription

            if not subscription_id:
                logger.error("No subscription in invoice")
                return

            # Find the user by subscription ID
            sub_result = await asyncio.to_thread(
                lambda: supabase.table('subscriptions')
                    .select('user_id')
                    .eq('stripe_subscription_id', subscription_id)
                    .execute()
            )

            if not sub_result.data:
                logger.error("No subscription found for invoice %s", invoice.id)
                return

            user_id = sub_result.data[0]['user_id']

            # Record transaction in database
            transaction_data = {
                'user_id': user_id,
                'stripe_invoice_id': invoice.id,
                'stripe_subscription_id': subscription_id,
                'amount': invoice.amount_paid,
                'currency': invoice.currency,
                'status': 'succeeded',
                'type': 'subscription_renewal',
                'payment_method': 'card',
                'metadata': {
                    'period_start': invoice.period_start,
                    'period_end': invoice.period_end,
                },
            }

            tx_result = await asyncio.to_thread(
                lambda: supabase.table('transactions').insert(transaction_data).execute()
            )

            if not tx_result.data:
                logger.error("Failed to record transaction for invoice %s", invoice.id)
            else:
                logger.info(
                    "Invoice %s payment recorded for user %s: %s %s",
                    invoice.id,
                    user_id,
                    invoice.amount_paid,
                    invoice.currency,
                )

            # Reset monthly usage counters for the new billing period
            # This ensures the user gets fresh quota for the new period
            # (Usage tracking tables accumulate usage; the quota check
            # queries the last 30 days, so this is implicitly handled,
            # but we log it for clarity.)
            logger.info("Usage quota refreshed for user %s (new billing period)", user_id)

        except Exception as e:
            logger.error("Error handling invoice payment succeeded: %s", e, exc_info=True)

    async def _handle_invoice_payment_failed(
        self,
        invoice: Any,  # stripe.Invoice
    ) -> None:
        """
        Handle failed invoice payment.
        Updates subscription to past_due and optionally triggers notification.
        """
        try:
            subscription_id = invoice.subscription

            if not subscription_id:
                logger.error("No subscription in invoice")
                return

            # Find the subscription to get user_id
            sub_result = await asyncio.to_thread(
                lambda: supabase.table('subscriptions')
                    .select('user_id')
                    .eq('stripe_subscription_id', subscription_id)
                    .execute()
            )

            if not sub_result.data:
                logger.error("No subscription found for failed invoice %s", invoice.id)
                return

            user_id = sub_result.data[0]['user_id']

            # Update subscription status to past_due
            update_result = await asyncio.to_thread(
                lambda: supabase.table('subscriptions')
                    .update({'status': 'past_due', 'updated_at': 'now()'})
                    .eq('stripe_subscription_id', subscription_id)
                    .execute()
            )

            if not update_result.data:
                logger.error("Failed to update subscription %s to past_due", subscription_id)
            else:
                logger.warning(
                    "Invoice payment failed for user %s, subscription %s. Status updated to past_due.",
                    user_id,
                    subscription_id,
                )

            # TODO: Trigger payment failure notification
            # e.g., await send_email(user_id, 'payment_failed', {
            #     'invoice_id': invoice.id,
            #     'amount': invoice.amount_due,
            #     'currency': invoice.currency,
            # })

        except Exception as e:
            logger.error("Error handling invoice payment failed: %s", e, exc_info=True)

    def _get_tier_from_price_id(self, price_id: str) -> Optional[str]:
        """
        Get subscription tier from price ID.

        Args:
            price_id: Stripe price ID

        Returns:
            Subscription tier name or None if not found
        """
        for tier, pid in self.PRICE_IDS.items():
            if pid == price_id:
                return tier
        return None

    def get_subscription_price_id(
        self,
        tier: Literal['free', 'starter', 'pro', 'enterprise'],
    ) -> Optional[str]:
        """
        Get Stripe price ID for a subscription tier.

        Args:
            tier: Subscription tier ('free', 'starter', 'pro', 'enterprise')

        Returns:
            Stripe price ID or None if not found
        """
        return self.PRICE_IDS.get(tier)

    def get_tier_level(self, tier: str) -> int:
        """
        Get numeric level for a subscription tier.

        Args:
            tier: Subscription tier name

        Returns:
            Numeric tier level (higher = more access)
        """
        return self.TIER_HIERARCHY.get(tier, 0)


# Global service instance (lazy initialization)
_stripe_service: Optional[StripePaymentService] = None


def get_stripe_service() -> Optional[StripePaymentService]:
    """
    Get or create the global Stripe payment service instance.

    Returns None if STRIPE_SECRET_KEY is not configured, allowing
    the payment strategy to fall back to x402-only mode.

    Returns:
        StripePaymentService instance, or None if Stripe is not configured
    """
    global _stripe_service
    if _stripe_service is None:
        api_key = os.environ.get("STRIPE_SECRET_KEY")
        if not api_key:
            logger.warning("STRIPE_SECRET_KEY not configured, Stripe payments disabled")
            return None
        try:
            _stripe_service = StripePaymentService()
        except ValueError as e:
            logger.error(f"Failed to initialize Stripe service: {e}")
            return None
    return _stripe_service


def reset_stripe_service() -> None:
    """Reset the global Stripe service instance (useful for testing)."""
    global _stripe_service
    _stripe_service = None


# Middleware for requiring active subscription
def subscription_required(min_tier: str = 'starter'):
    """
    Decorator to require an active subscription for an endpoint.

    Uses request['user'] and request['agent'] set by the auth middleware
    (not request['user_id'] which is never set).

    FAILS CLOSED: If subscription check fails due to error, access is denied
    with a 503 status. This is the secure default for a payment system.

    Args:
        min_tier: Minimum subscription tier required ('free', 'starter', 'pro', 'enterprise')
    """
    def decorator(handler):
        async def wrapper(request: web.Request) -> web.Response:
            service = get_stripe_service()

            # Try to get user or agent identity from auth middleware
            user = request.get('user')
            agent = request.get('agent')

            if not user and not agent:
                return web.json_response(
                    {'error': 'Authentication required'},
                    status=401,
                )

            # Agent authentication: check subscription_tier from agent record
            if agent:
                tier = agent.get('subscription_tier', 'free')
                if service and service.get_tier_level(tier) >= service.get_tier_level(min_tier):
                    return await handler(request)
                else:
                    return web.json_response({
                        'error': f'Subscription tier {min_tier} or higher required',
                        'current_tier': tier,
                        'required_tier': min_tier,
                    }, status=403)

            # User authentication: check subscription in database
            if user:
                user_id = str(user.id) if hasattr(user, 'id') else None
                if not user_id:
                    return web.json_response(
                        {'error': 'Unable to determine user identity'},
                        status=401,
                    )

                try:
                    result = await asyncio.to_thread(
                        lambda: supabase.table('subscriptions')
                            .select('plan,status')
                            .eq('user_id', user_id)
                            .execute()
                    )

                    if result.data:
                        sub = result.data[0]
                        if sub['status'] in ('active', 'trialing'):
                            tier = sub.get('plan', 'free')
                            if service and service.get_tier_level(tier) >= service.get_tier_level(min_tier):
                                return await handler(request)
                            else:
                                return web.json_response({
                                    'error': f'Insufficient subscription tier. Required: {min_tier}, Current: {tier}',
                                    'required_tier': min_tier,
                                    'current_tier': tier,
                                }, status=403)

                    # No active subscription found
                    return web.json_response({
                        'error': 'Active subscription required',
                        'required_tier': min_tier,
                    }, status=402)

                except Exception as e:
                    logger.error(f"Error checking subscription (fail-closed): {e}")
                    # FAIL CLOSED: deny access on database error
                    return web.json_response({
                        'error': 'Unable to verify subscription status',
                    }, status=503)

            return web.json_response({'error': 'Authentication required'}, status=401)
        return wrapper
    return decorator

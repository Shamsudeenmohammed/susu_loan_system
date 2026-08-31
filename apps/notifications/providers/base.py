from abc import ABC, abstractmethod


class SMSProvider(ABC):
    """
    Abstract interface for an SMS provider.

    The rest of the application talks to this interface (via the provider
    factory), never to a concrete provider directly. This keeps the
    architecture provider-independent so another SMS provider can be added
    later without rewriting the application.
    """

    name = 'base'

    @abstractmethod
    def send_sms(self, phone_number, message, sender_id=None):
        """
        Send a single SMS message.

        Args:
            phone_number: E.164/normalized destination phone number.
            message: The message body.
            sender_id: Optional override for the sender/From id.

        Returns:
            dict with at least:
                - 'provider_message_id': provider id for the message (str)
                - 'status': provider-level delivery status (str)

        Raises:
            SMSProviderError (or subclass) on failure so the caller can
            handle retries and logging.
        """
        raise NotImplementedError

    @abstractmethod
    def get_message_status(self, provider_message_id):
        """
        Poll the delivery status of a previously sent message.

        Args:
            provider_message_id: id returned by send_sms.

        Returns:
            str: provider-level delivery status.
        """
        raise NotImplementedError

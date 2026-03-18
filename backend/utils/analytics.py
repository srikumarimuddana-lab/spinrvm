"""
Analytics Integration for Spinr
Supports Mixpanel and Amplitude for event tracking and user analytics.
"""
from typing import Optional, Dict, Any, List
from loguru import logger
import hashlib


class MixpanelService:
    """Service for Mixpanel analytics."""
    
    def __init__(self, token: str = ''):
        """
        Initialize Mixpanel service.
        
        Args:
            token: Mixpanel project token
        """
        self.token = token
        self.configured = bool(token)
        
        if self.configured:
            try:
                import mixpanel
                self._client = mixpanel.Mixpanel(token)
                logger.info('Mixpanel configured successfully')
            except ImportError:
                logger.warning('Mixpanel package not installed - using mock mode')
                self.configured = False
                self._client = None
        else:
            logger.warning('Mixpanel not configured - using mock mode')
            self._client = None
    
    def track(
        self,
        distinct_id: str,
        event_name: str,
        properties: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Track an event.
        
        Args:
            distinct_id: Unique identifier for the user
            event_name: Name of the event to track
            properties: Optional event properties
        
        Returns:
            True if tracking succeeded
        """
        if not self.configured:
            logger.debug(f'[MOCK Mixpanel] {event_name}: {distinct_id} - {properties}')
            return True
        
        try:
            self._client.track(distinct_id, event_name, properties)
            return True
        except Exception as e:
            logger.error(f'Failed to track Mixpanel event: {e}')
            return False
    
    def people_set(
        self,
        distinct_id: str,
        properties: Dict[str, Any]
    ) -> bool:
        """
        Set user properties.
        
        Args:
            distinct_id: Unique identifier for the user
            properties: User properties to set
    
        Returns:
            True if setting succeeded
        """
        if not self.configured:
            logger.debug(f'[MOCK Mixpanel] Set people: {distinct_id} - {properties}')
            return True
        
        try:
            self._client.people_set(distinct_id, properties)
            return True
        except Exception as e:
            logger.error(f'Failed to set Mixpanel people properties: {e}')
            return False
    
    def people_increment(
        self,
        distinct_id: str,
        property_name: str,
        value: float
    ) -> bool:
        """
        Increment a user property.
        
        Args:
            distinct_id: Unique identifier for the user
            property_name: Name of the property to increment
            value: Amount to increment by
    
        Returns:
            True if increment succeeded
        """
        if not self.configured:
            logger.debug(f'[MOCK Mixpanel] Increment {property_name} by {value}: {distinct_id}')
            return True
        
        try:
            self._client.people_increment(distinct_id, property_name, value)
            return True
        except Exception as e:
            logger.error(f'Failed to increment Mixpanel property: {e}')
            return False
    
    def alias(self, old_id: str, new_id: str) -> bool:
        """
        Create an alias for a user (e.g., anonymous to identified).
        
        Args:
            old_id: Previous identifier (e.g., anonymous ID)
            new_id: New identifier (e.g., user ID)
    
        Returns:
            True if alias succeeded
        """
        if not self.configured:
            logger.debug(f'[MOCK Mixpanel] Alias {old_id} -> {new_id}')
            return True
        
        try:
            self._client.alias(old_id, new_id)
            return True
        except Exception as e:
            logger.error(f'Failed to create Mixpanel alias: {e}')
            return False


class AmplitudeService:
    """Service for Amplitude analytics."""
    
    def __init__(self, api_key: str = ''):
        """
        Initialize Amplitude service.
        
        Args:
            api_key: Amplitude API key
        """
        self.api_key = api_key
        self.configured = bool(api_key)
        
        if self.configured:
            try:
                import amplitude
                self._client = amplitude.Amplitude()
                self._client.init(api_key)
                logger.info('Amplitude configured successfully')
            except ImportError:
                logger.warning('Amplitude package not installed - using mock mode')
                self.configured = False
                self._client = None
        else:
            logger.warning('Amplitude not configured - using mock mode')
            self._client = None
    
    def track(
        self,
        user_id: str,
        event_type: str,
        event_properties: Optional[Dict[str, Any]] = None,
        user_properties: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Track an event.
        
        Args:
            user_id: Unique identifier for the user
            event_type: Type of event to track
            event_properties: Optional event properties
            user_properties: Optional user properties to set
    
        Returns:
            True if tracking succeeded
        """
        if not self.configured:
            logger.debug(f'[MOCK Amplitude] {event_type}: {user_id} - {event_properties}')
            return True
        
        try:
            from amplitude import BaseEvent
            
            event = BaseEvent(
                event_type=event_type,
                user_id=user_id,
                event_properties=event_properties,
                user_properties=user_properties
            )
            
            self._client.track(event)
            return True
        except Exception as e:
            logger.error(f'Failed to track Amplitude event: {e}')
            return False
    
    def identify(
        self,
        user_id: str,
        properties: Dict[str, Any]
    ) -> bool:
        """
        Identify a user with properties.
        
        Args:
            user_id: Unique identifier for the user
            properties: User properties
    
        Returns:
            True if identify succeeded
        """
        if not self.configured:
            logger.debug(f'[MOCK Amplitude] Identify: {user_id} - {properties}')
            return True
        
        try:
            from amplitude import Identify, BaseEvent
            
            identify_obj = Identify()
            for key, value in properties.items():
                identify_obj.set(key, value)
            
            event = BaseEvent(
                event_type='$identify',
                user_id=user_id,
                user_properties=properties
            )
            
            self._client.track(event)
            return True
        except Exception as e:
            logger.error(f'Failed to identify Amplitude user: {e}')
            return False
    
    def group_identify(
        self,
        group_type: str,
        group_id: str,
        properties: Dict[str, Any]
    ) -> bool:
        """
        Identify a group with properties.
        
        Args:
            group_type: Type of group (e.g., 'company', 'team')
            group_id: Unique identifier for the group
            properties: Group properties
    
        Returns:
            True if group identify succeeded
        """
        if not self.configured:
            logger.debug(f'[MOCK Amplitude] Group identify: {group_type}:{group_id} - {properties}')
            return True
        
        try:
            from amplitude import Identify, BaseEvent
            
            identify_obj = Identify()
            for key, value in properties.items():
                identify_obj.set(key, value)
            
            event = BaseEvent(
                event_type='$groupidentify',
                group_type=group_type,
                group_id=group_id,
                group_properties=properties
            )
            
            self._client.track(event)
            return True
        except Exception as e:
            logger.error(f'Failed to identify Amplitude group: {e}')
            return False


class AnalyticsService:
    """Unified analytics service supporting multiple providers."""
    
    def __init__(
        self,
        mixpanel_token: str = '',
        amplitude_api_key: str = '',
        enable_mixpanel: bool = True,
        enable_amplitude: bool = True
    ):
        """
        Initialize unified analytics service.
        
        Args:
            mixpanel_token: Mixpanel project token
            amplitude_api_key: Amplitude API key
            enable_mixpanel: Whether to enable Mixpanel
            enable_amplitude: Whether to enable Amplitude
        """
        self.mixpanel = MixpanelService(mixpanel_token) if enable_mixpanel else None
        self.amplitude = AmplitudeService(amplitude_api_key) if enable_amplitude else None
        
        logger.info(f'Analytics initialized: Mixpanel={self.mixpanel is not None}, Amplitude={self.amplitude is not None}')
    
    def track(
        self,
        user_id: str,
        event_name: str,
        properties: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Track an event on all enabled providers.
        
        Args:
            user_id: Unique identifier for the user
            event_name: Name of the event
            properties: Optional event properties
    
        Returns:
            True if tracking succeeded on at least one provider
        """
        success = False
        
        if self.mixpanel:
            success = self.mixpanel.track(user_id, event_name, properties) or success
        
        if self.amplitude:
            success = self.amplitude.track(user_id, event_name, properties) or success
        
        return success
    
    def identify_user(
        self,
        user_id: str,
        properties: Dict[str, Any]
    ) -> bool:
        """
        Identify a user with properties.
        
        Args:
            user_id: Unique identifier for the user
            properties: User properties
    
        Returns:
            True if identify succeeded on at least one provider
        """
        success = False
        
        if self.mixpanel:
            success = self.mixpanel.people_set(user_id, properties) or success
        
        if self.amplitude:
            success = self.amplitude.identify(user_id, properties) or success
        
        return success
    
    # Common event tracking methods
    def track_ride_requested(self, user_id: str, ride_data: Dict[str, Any]) -> bool:
        """Track ride request event."""
        return self.track(user_id, 'Ride Requested', ride_data)
    
    def track_ride_completed(self, user_id: str, ride_data: Dict[str, Any]) -> bool:
        """Track ride completion event."""
        return self.track(user_id, 'Ride Completed', ride_data)
    
    def track_ride_cancelled(self, user_id: str, ride_data: Dict[str, Any]) -> bool:
        """Track ride cancellation event."""
        return self.track(user_id, 'Ride Cancelled', ride_data)
    
    def track_payment_processed(self, user_id: str, payment_data: Dict[str, Any]) -> bool:
        """Track payment processing event."""
        return self.track(user_id, 'Payment Processed', payment_data)
    
    def track_driver_online(self, driver_id: str, location: Dict[str, float]) -> bool:
        """Track driver going online."""
        return self.track(driver_id, 'Driver Online', location)
    
    def track_driver_offline(self, driver_id: str) -> bool:
        """Track driver going offline."""
        return self.track(driver_id, 'Driver Offline')
    
    def track_signup(self, user_id: str, signup_data: Dict[str, Any]) -> bool:
        """Track user signup event."""
        return self.track(user_id, 'User Signup', signup_data)
    
    def track_login(self, user_id: str, login_data: Dict[str, Any]) -> bool:
        """Track user login event."""
        return self.track(user_id, 'User Login', login_data)


# Global analytics instance
_analytics: Optional[AnalyticsService] = None


def get_analytics() -> AnalyticsService:
    """Get or create the global analytics instance."""
    global _analytics
    if _analytics is None:
        _analytics = AnalyticsService()
    return _analytics


def init_analytics(
    mixpanel_token: str = '',
    amplitude_api_key: str = ''
) -> AnalyticsService:
    """
    Initialize global analytics service.
    
    Args:
        mixpanel_token: Mixpanel project token
        amplitude_api_key: Amplitude API key
    
    Returns:
        Configured AnalyticsService instance
    """
    global _analytics
    _analytics = AnalyticsService(
        mixpanel_token=mixpanel_token,
        amplitude_api_key=amplitude_api_key
    )
    return _analytics
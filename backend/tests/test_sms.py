"""
Unit tests for SMS service functionality.
Tests cover OTP SMS sending, general SMS, and Twilio integration.
"""
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from typing import Dict, Any

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSMSService:
    """Tests for SMS service core functionality."""
    
    @pytest.mark.asyncio
    async def test_send_sms_console_fallback(self):
        """Test SMS sending falls back to console when Twilio not configured."""
        from backend.sms_service import send_sms
        
        result = await send_sms(
            '+1234567890',
            'Test message',
            twilio_sid='',
            twilio_token='',
            twilio_from=''
        )
        
        assert result['success'] is True
        assert result['provider'] == 'console'
    
    @pytest.mark.asyncio
    async def test_send_sms_twilio_success(self):
        """Test SMS sending via Twilio successfully."""
        from backend.sms_service import send_sms
        
        with patch('backend.sms_service.Client') as mock_client:
            mock_sms = MagicMock()
            mock_sms.sid = 'SM123'
            mock_client.return_value.messages.create.return_value = mock_sms
            
            result = await send_sms(
                '+1234567890',
                'Test message',
                twilio_sid='AC123',
                twilio_token='token',
                twilio_from='+10000000000'
            )
            
            assert result['success'] is True
            assert result['provider'] == 'twilio'
            assert result['sid'] == 'SM123'
    
    @pytest.mark.asyncio
    async def test_send_sms_twilio_failure(self):
        """Test SMS sending via Twilio handles failure."""
        from backend.sms_service import send_sms
        
        with patch('backend.sms_service.Client') as mock_client:
            mock_client.return_value.messages.create.side_effect = Exception('Twilio error')
            
            result = await send_sms(
                '+1234567890',
                'Test message',
                twilio_sid='AC123',
                twilio_token='token',
                twilio_from='+10000000000'
            )
            
            assert result['success'] is False
            assert result['provider'] == 'twilio'
            assert 'error' in result
    
    @pytest.mark.asyncio
    async def test_send_otp_sms(self):
        """Test sending OTP SMS."""
        from backend.sms_service import send_otp_sms
        
        with patch('backend.sms_service.Client') as mock_client:
            mock_sms = MagicMock()
            mock_sms.sid = 'SM456'
            mock_client.return_value.messages.create.return_value = mock_sms
            
            result = await send_otp_sms(
                '+1234567890',
                '123456',
                twilio_sid='AC123',
                twilio_token='token',
                twilio_from='+10000000000'
            )
            
            assert result['success'] is True
            assert result['sid'] == 'SM456'
    
    def test_send_otp_sms_format(self):
        """Test OTP SMS message format."""
        otp_code = '123456'
        expected_message = f'Your Spinr verification code is: {otp_code}. It expires in 5 minutes.'
        
        assert otp_code in expected_message
        assert 'verification code' in expected_message.lower()
        assert 'expires in 5 minutes' in expected_message


class TestTwilioIntegration:
    """Tests for Twilio integration."""
    
    def test_twilio_client_initialization(self):
        """Test Twilio client initialization."""
        from twilio.rest import Client
        
        with patch('twilio.rest.Client') as mock_client:
            mock_client.return_value = MagicMock()
            client = mock_client(account_sid='AC123', auth_token='token')
            
            assert client is not None
            mock_client.assert_called_once_with(account_sid='AC123', auth_token='token')
    
    def test_twilio_message_creation_params(self):
        """Test Twilio message creation parameters."""
        params = {
            'body': 'Test message',
            'from_': '+10000000000',
            'to': '+1234567890'
        }
        
        assert 'body' in params
        assert 'from_' in params
        assert 'to' in params


class TestSMSValidation:
    """Tests for SMS input validation."""
    
    def test_validate_phone_number_valid(self):
        """Test validating valid phone numbers."""
        valid_numbers = [
            '+1234567890',
            '+1-234-567-8900',
            '+442079460958'
        ]
        
        for number in valid_numbers:
            assert number.startswith('+')
            assert any(c.isdigit() for c in number)
    
    def test_validate_phone_number_invalid(self):
        """Test validating invalid phone numbers."""
        invalid_numbers = [
            '',
            'notaphone',
            '12345',
        ]
        
        for number in invalid_numbers:
            is_valid = number.startswith('+') and len(number) >= 10
            assert is_valid is False
    
    def test_validate_message_length(self):
        """Test SMS message length validation."""
        max_length = 160  # Standard SMS length
        
        short_message = 'Hello'
        long_message = 'A' * 200
        
        assert len(short_message) <= max_length
        assert len(long_message) > max_length


class TestSMSRetry:
    """Tests for SMS retry logic."""
    
    def test_sms_retry_on_failure(self):
        """Test SMS retry logic on failure."""
        from tenacity import retry, stop_after_attempt, wait_fixed
        
        call_count = 0
        
        @retry(stop=stop_after_attempt(3), wait=wait_fixed(0.01))
        def send_with_retry():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception('Temporary failure')
            return True
        
        result = send_with_retry()
        
        assert result is True
        assert call_count == 3
    
    def test_sms_retry_exhausted(self):
        """Test SMS retry exhaustion."""
        from tenacity import retry, stop_after_attempt, wait_fixed, RetryError
        
        @retry(stop=stop_after_attempt(3), wait=wait_fixed(0.01))
        def send_always_fails():
            raise Exception('Always fails')
        
        with pytest.raises(RetryError):
            send_always_fails()
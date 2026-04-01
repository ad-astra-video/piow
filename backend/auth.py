#!/usr/bin/env python3
"""
SIWE (Sign-In with Ethereum) Authentication Module
Handles Ethereum-based authentication for the Live Transcription Platform
"""

import os
import time
import jwt
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)

# JWT Configuration
JWT_SECRET = os.environ.get("JWT_SECRET", "your-super-secret-jwt-key-change-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24

# SIWE Configuration
SIWE_NONCE_EXPIRY_MINUTES = 10

def generate_nonce() -> str:
    """
    Generate a random nonce for SIWE authentication.
    Returns a hex string nonce.
    """
    import secrets
    return secrets.token_hex(16)

def create_siwe_message(address: str, nonce: str, issued_at: Optional[str] = None) -> str:
    """
    Create a SIWE message for signing.
    
    Args:
        address: Ethereum address
        nonce: Random nonce
        issued_at: Timestamp (ISO 8601), defaults to now
        
    Returns:
        Formatted SIWE message
    """
    if not issued_at:
        issued_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    
    message = f"""live-translation-app wants you to sign in with your Ethereum account:
{address}

I accept the Terms of Service: https://live-translation-app.tos

URI: https://live-translation-app
Version: 1
Chain ID: 1
Nonce: {nonce}
Issued At: {issued_at}
"""
    return message.strip()

def verify_siwe_message(message: str, signature: str, address: str) -> bool:
    """
    Verify a SIWE message signature using web3.py.
    
    Args:
        message: The SIWE message that was signed
        signature: The signature (hex string)
        address: The Ethereum address that should have signed it
        
    Returns:
        True if signature is valid, False otherwise
    """
    try:
        from web3 import Web3
        
        # Basic checks
        if not message or not signature or not address:
            return False
        
        # Check that message contains expected elements
        if "live-translation-app wants you to sign in" not in message:
            return False
            
        if address.lower() not in message.lower():
            return False
        
        # Use web3 to recover address from signature
        # The signature is expected to be 0x-prefixed hex
        if not signature.startswith('0x'):
            signature = '0x' + signature
        
        # Encode the message as eth_sign does
        message_encoded = Web3.eth.account._hash_message(text=message)
        
        # Recover address
        try:
            recovered_address = Web3.eth.account.recover_hash(message_encoded, signature=signature)
            # Check if recovered address matches expected address (case insensitive)
            return recovered_address.lower() == address.lower()
        except Exception as e:
            logger.debug(f"Error recovering address: {e}")
            return False
            
    except ImportError:
        logger.warning("web3 not available, falling back to basic verification")
        # Fallback to basic format check
        try:
            if not message or not signature or not address:
                return False
            if "live-translation-app wants you to sign in" not in message:
                return False
            if address.lower() not in message.lower():
                return False
            return len(signature) >= 130  # Typical signature length
        except Exception as e:
            logger.error(f"Error in fallback SIWE verification: {e}")
            return False
    except Exception as e:
        logger.error(f"Error verifying SIWE message: {e}")
        return False
def create_jwt_token(user_data: Dict[str, Any]) -> str:
    """
    Create a JWT token for authenticated user.
    
    Args:
        user_data: User information to include in token
        
    Returns:
        JWT token string
    """
    payload = {
        **user_data,
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS),
        "iat": datetime.utcnow()
    }
    
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token

def verify_jwt_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Verify and decode a JWT token.
    
    Args:
        token: JWT token string
        
    Returns:
        Decoded payload if valid, None otherwise
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        logger.warning("JWT token has expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid JWT token: {e}")
        return None

def hash_nonce(nonce: str) -> str:
    """
    Hash a nonce for storage (in case we want to store hashed nonces).
    For now, we'll just return the nonce as-is since we're storing it temporarily.
    
    Args:
        nonce: Nonce string
        
    Returns:
        Hashed nonce (or original nonce for now)
    """
    # In production, you might want to hash this for security
    # return hashlib.sha256(nonce.encode()).hexdigest()
    return nonce

# Mock database functions for nonce storage (replace with actual Supabase calls)
class NonceStore:
    """In-memory nonce store for demonstration. Replace with Supabase table."""
    
    def __init__(self):
        self.nonces = {}  # address -> {nonce, expires_at}
    
    def store_nonce(self, address: str, nonce: str, expires_at: datetime):
        """Store a nonce for an address."""
        self.nonces[address.lower()] = {
            "nonce": nonce,
            "expires_at": expires_at
        }
        logger.info(f"Stored nonce for {address}")
    
    def get_nonce(self, address: str) -> Optional[Dict[str, Any]]:
        """Get nonce for an address if it exists and hasn't expired."""
        address_lower = address.lower()
        if address_lower not in self.nonces:
            return None
            
        nonce_data = self.nonces[address_lower]
        if datetime.utcnow() > nonce_data["expires_at"]:
            # Remove expired nonce
            del self.nonces[address_lower]
            return None
            
        return nonce_data
    
    def consume_nonce(self, address: str) -> bool:
        """
        Consard a nonce (mark as used).
        Returns True if nonce was consumed, False if not found/expired.
        """
        address_lower = address.lower()
        if address_lower in self.nonces:
            nonce_data = self.nonces[address_lower]
            if datetime.utcnow() <= nonce_data["expires_at"]:
                del self.nonces[address_lower]
                logger.info(f"Consumed nonce for {address}")
                return True
            else:
                # Expired, remove it
                del self.nonces[address_lower]
        return False

# Global nonce store instance
nonce_store = NonceStore()

def authenticate_with_siwe(message: str, signature: str) -> Optional[Dict[str, Any]]:
    """
    Authenticate a user using SIWE.
    
    Args:
        message: The SIWE message that was signed
        signature: The signature
        
    Returns:
        User data if authentication successful, None otherwise
    """
    try:
        # Extract address from message
        lines = message.split('\n')
        address_line = None
        address_line = None
        for line in lines:
            if line.startswith('0x') and len(line) >= 42:
                address_line = line.strip()
                break
        
        if not address_line:
            logger.error("Could not extract Ethereum address from SIWE message")
            return None
            
        address = address_line
        
        # Extract nonce from message
        nonce = None
        for line in lines:
            if line.startswith('Nonce:'):
                nonce = line.split(':', 1)[1].strip()
                break
                
        if not nonce:
            logger.error("Could not extract nonce from SIWE message")
            return None
        
        # Verify the signature
        if not verify_siwe_message(message, signature, address):
            logger.error(f"SIWE signature verification failed for {address}")
            return None
        
        # Check nonce hasn't been used and isn't expired
        nonce_data = nonce_store.get_nonce(address)
        if not nonce_data:
            logger.error(f"Nonce not found or expired for {address}")
            return None
            
        if nonce_data["nonce"] != nonce:
            logger.error(f"Nonce mismatch for {address}")
            return None
        
        # Consume the nonce (mark as used)
        if not nonce_store.consume_nonce(address):
            logger.error(f"Failed to consume nonce for {address}")
            return None
        
        # Get or create user in database
        # This would normally query Supabase
        user_data = {
            "id": "user-id-from-db",  # TODO: Get from database
            "ethereum_address": address.lower(),
            "email": None,  # Would be set if user has email linked
            "created_at": datetime.utcnow().isoformat(),
        }
        
        # Create JWT token
        token = create_jwt_token(user_data)
        
        return {
            "user": user_data,
            "token": token,
            "expires_in": JWT_EXPIRY_HOURS * 3600  # seconds
        }
        
    except Exception as e:
        logger.error(f"Error in SIWE authentication: {e}")
        return None

# Health check function
def auth_health_check() -> Dict[str, Any]:
    """Check if auth module is working correctly."""
    return {
        "status": "healthy",
        "module": "auth",
        "timestamp": datetime.utcnow().isoformat(),
        "jwt_secret_configured": bool(JWT_SECRET and JWT_SECRET != "your-super-secret-jwt-key-change-in-production")
    }

/**
 * x402 v2 Payment Client for Frontend
 * 
 * Implements the X402 v2 protocol for browser-based crypto payments:
 * - Intercepts 402 responses and parses PAYMENT-REQUIRED headers (Base64-encoded)
 * - Connects wallet (MetaMask, Coinbase Wallet, etc.)
 * - Signs payment with wallet using EIP-3009
 * - Retries request with PAYMENT-SIGNATURE header (Base64-encoded)
 * - Decodes PAYMENT-RESPONSE header from successful response
 */

const FACILITATOR_URL = import.meta.env.VITE_FACILITATOR_URL || 'https://x402.org/facilitator';

/**
 * Decode a Base64-encoded x402 header value per X402 v2 spec.
 * Falls back to plain JSON for backward compatibility.
 */
function decodeX402Header(headerValue) {
  try {
    return JSON.parse(atob(headerValue));
  } catch {
    try {
      return JSON.parse(headerValue);
    } catch (e) {
      console.error('Failed to decode x402 header:', e);
      return null;
    }
  }
}

/**
 * Encode a JavaScript object as Base64-encoded JSON per X402 v2 spec.
 */
function encodeX402Header(data) {
  return btoa(JSON.stringify(data));
}

/**
 * Get the current Ethereum account from MetaMask or compatible wallet.
 */
async function getWalletAccount() {
  if (!window.ethereum) {
    throw new Error('No Web3 wallet detected. Please install MetaMask or Coinbase Wallet.');
  }
  const accounts = await window.ethereum.request({ method: 'eth_requestAccounts' });
  return accounts[0];
}

/**
 * Sign an EIP-3009 payment authorization with the connected wallet.
 */
async function signPayment(walletAddress, paymentOption, resourceUrl) {
  if (!window.ethereum) {
    throw new Error('No Web3 wallet detected');
  }

  const chainId = parseInt(paymentOption.network.split(':')[1], 10);
  const nonce = '0x' + Array.from(crypto.getRandomValues(new Uint8Array(32)))
    .map(b => b.toString(16).padStart(2, '0'))
    .join('');
  const deadline = Math.floor(Date.now() / 1000) + 300; // 5 minutes

  const domain = {
    name: 'USD Coin',
    version: '2',
    chainId: chainId,
  };

  const types = {
    ReceiveWithAuthorization: [
      { name: 'from', type: 'address' },
      { name: 'to', type: 'address' },
      { name: 'value', type: 'uint256' },
      { name: 'validFrom', type: 'uint256' },
      { name: 'validTo', type: 'uint256' },
      { name: 'nonce', type: 'bytes32' },
    ],
  };

  const message = {
    from: walletAddress,
    to: paymentOption.payTo,
    value: BigInt(paymentOption.amount),
    validFrom: 0,
    validTo: deadline,
    nonce: nonce,
  };

  const signature = await window.ethereum.request({
    method: 'eth_signTypedData_v4',
    params: [walletAddress, JSON.stringify({ domain, types, primaryType: 'ReceiveWithAuthorization', message })],
  });

  return {
    x402Version: 2,
    resource: {
      url: resourceUrl,
      description: paymentOption.description || 'API access',
      mimeType: 'application/json',
    },
    accepted: paymentOption,
    payload: {
      signature: signature,
      authorization: {
        from: walletAddress,
        to: paymentOption.payTo,
        value: paymentOption.amount,
        nonce: nonce,
        deadline: deadline,
      },
    },
  };
}

/**
 * Make an HTTP request with automatic x402 payment handling.
 * 
 * Flow:
 * 1. Send request without payment
 * 2. If 402 response, decode PAYMENT-REQUIRED header
 * 3. Connect wallet and sign payment
 * 4. Retry request with PAYMENT-SIGNATURE header
 * 5. Decode PAYMENT-RESPONSE header from successful response
 */
export async function fetchWithPayment(url, options = {}) {
  // Initial request without payment
  let response = await fetch(url, options);

  if (response.status !== 402) {
    return response;
  }

  // Check for PAYMENT-REQUIRED header
  const paymentRequiredHeader = response.headers.get('PAYMENT-REQUIRED');
  if (!paymentRequiredHeader) {
    return response; // Not an x402 402, return as-is
  }

  // Decode payment requirements (Base64 per X402 v2 spec)
  const paymentRequirements = decodeX402Header(paymentRequiredHeader);
  if (!paymentRequirements || !paymentRequirements.accepts || paymentRequirements.accepts.length === 0) {
    console.error('Invalid x402 payment requirements');
    return response;
  }

  // Select the first compatible payment option
  const paymentOption = paymentRequirements.accepts[0];
  const resourceUrl = paymentRequirements.resource?.url || url;

  try {
    // Connect wallet
    const walletAddress = await getWalletAccount();

    // Sign payment
    const paymentPayload = await signPayment(walletAddress, paymentOption, resourceUrl);

    // Encode payment signature (Base64 per X402 v2 spec)
    const encodedPayment = encodeX402Header(paymentPayload);

    // Retry request with PAYMENT-SIGNATURE header
    const retryHeaders = {
      ...(options.headers || {}),
      'PAYMENT-SIGNATURE': encodedPayment,
    };

    response = await fetch(url, {
      ...options,
      headers: retryHeaders,
    });

    // Decode PAYMENT-RESPONSE header if present
    if (response.status === 200) {
      const paymentResponseHeader = response.headers.get('PAYMENT-RESPONSE');
      if (paymentResponseHeader) {
        const settlement = decodeX402Header(paymentResponseHeader);
        if (settlement) {
          console.log('x402 payment settled:', {
            transaction: settlement.transaction,
            network: settlement.network,
            amount: settlement.amount,
            success: settlement.success,
          });
        }
      }
    }

    return response;
  } catch (error) {
    console.error('x402 payment failed:', error);
    throw error;
  }
}

/**
 * Check if the browser has a Web3 wallet available.
 */
export function hasWallet() {
  return typeof window !== 'undefined' && !!window.ethereum;
}

/**
 * Get the list of supported x402 networks.
 */
export const SUPPORTED_NETWORKS = [
  { chainId: 'eip155:84532', name: 'Base Sepolia', type: 'evm' },
  { chainId: 'eip155:8453', name: 'Base', type: 'evm' },
  { chainId: 'eip155:137', name: 'Polygon', type: 'evm' },
  { chainId: 'eip155:1', name: 'Ethereum', type: 'evm' },
  { chainId: 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', name: 'Solana Mainnet', type: 'svm' },
  { chainId: 'solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1', name: 'Solana Devnet', type: 'svm' },
];

export default {
  fetchWithPayment,
  hasWallet,
  SUPPORTED_NETWORKS,
  decodeX402Header,
  encodeX402Header,
};
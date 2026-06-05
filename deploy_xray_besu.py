#!/usr/bin/env python3
"""
Deploy 1 StateBloater contract for XRAY Besu evaluation.
Writes contract address into networkconfig_xray_besu.json.

Worker accounts rely on gasPrice=0 (Besu --min-gas-price=0) — no funding needed.
"""
import json
from web3 import Web3

w3 = Web3(Web3.HTTPProvider('http://localhost:8545'))
if not w3.is_connected():
    print('ERROR: Cannot connect to node at localhost:8545')
    raise SystemExit(1)

print(f'Connected. Block: {w3.eth.block_number}')

with open('StateBloater.json', 'r') as f:
    artifact = json.load(f)

private_key = '0x8f2a55949038a9610f502c24114d051185071191bc20b60811a2d7fba4513689'
account = w3.eth.account.from_key(private_key)
print(f'Deployer: {account.address}  Balance: {w3.from_wei(w3.eth.get_balance(account.address), "ether"):.2f} ETH')

print('Deploying 1 StateBloater contract...')
Contract = w3.eth.contract(abi=artifact['abi'], bytecode=artifact['bytecode'])
nonce = w3.eth.get_transaction_count(account.address)
tx = Contract.constructor().build_transaction({
    'from': account.address,
    'nonce': nonce,
    'gas': 8000000,
    'gasPrice': w3.to_wei(1, 'gwei'),
    'chainId': 1337,
})
signed = account.sign_transaction(tx)
tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
print(f'  Deployment tx submitted: {tx_hash.hex()}')
receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
addr = receipt.contractAddress
print(f'  Contract Address: {addr}')

with open('networkconfig_xray_besu.json', 'r') as f:
    config = json.load(f)

config['ethereum']['contracts'] = {
    'SB0': {
        'address': addr,
        'abi': artifact['abi'],
        'gas': {'gasLimit': 8000000}
    }
}

with open('networkconfig_xray_besu.json', 'w') as f:
    json.dump(config, f, indent=2)

print(f'networkconfig_xray_besu.json updated with SB0 at {addr}')

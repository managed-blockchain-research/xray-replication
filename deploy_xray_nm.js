#!/usr/bin/env node
/**
 * Deploy 1 StateBloater contract for XRAY Nethermind evaluation.
 * Writes contract address into networkconfig_xray_nm.json.
 * Uses the spaceneth pre-funded account (private key 0x000...001).
 */
'use strict';

const ethers = require('ethers');
const fs = require('fs');

async function deploy() {
    const provider = new ethers.JsonRpcProvider('http://127.0.0.1:8545');
    const network = await provider.getNetwork();
    const chainId = Number(network.chainId);

    const privateKey = '0x' + '1'.padStart(64, '0');
    const wallet = new ethers.Wallet(privateKey, provider);
    console.log(`Connected. ChainId: ${chainId}. Deployer: ${wallet.address}`);

    const artifact = JSON.parse(fs.readFileSync('./StateBloater.json', 'utf8'));
    const factory = new ethers.ContractFactory(artifact.abi, artifact.bytecode, wallet);

    const nonce = await provider.getTransactionCount(wallet.address);
    const deployTx = await factory.getDeployTransaction();
    const tx = {
        type: 0,
        data: deployTx.data,
        gasLimit: 8000000,
        gasPrice: ethers.parseUnits('1', 'gwei'),
        chainId: chainId,
        nonce: nonce,
    };
    const signed = await wallet.signTransaction(tx);
    const response = await provider.broadcastTransaction(signed);
    console.log(`  Deployment tx submitted: ${response.hash}`);

    const receipt = await provider.waitForTransaction(response.hash, 1, 120000);
    const addr = receipt.contractAddress;
    console.log(`  Contract Address: ${addr}`);

    const config = JSON.parse(fs.readFileSync('./networkconfig_xray_nm.json', 'utf8'));
    config.ethereum.contracts = {
        SB0: {
            address: addr,
            abi: artifact.abi,
            gas: { gasLimit: 8000000 }
        }
    };

    fs.writeFileSync('./networkconfig_xray_nm.json', JSON.stringify(config, null, 2));
    console.log(`networkconfig_xray_nm.json updated with SB0 at ${addr}`);
}

deploy().catch(err => { console.error(err); process.exit(1); });

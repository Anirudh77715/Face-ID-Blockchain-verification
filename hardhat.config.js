/**
 * Hardhat is infrastructure only: it compiles the contract and runs the local chain.
 * Deployment and all calls go through pom/chain.py via web3, so the pipeline stays a
 * single language and a judge never has to run a JS script to reproduce a result.
 */
require("hardhat/config");

module.exports = {
  solidity: {
    version: "0.8.24",
    settings: { optimizer: { enabled: true, runs: 200 } },
  },
  paths: { sources: "./contracts", artifacts: "./artifacts" },
  networks: {
    hardhat: { chainId: 31337 },
    localhost: { url: "http://127.0.0.1:8545", chainId: 31337 },
  },
};

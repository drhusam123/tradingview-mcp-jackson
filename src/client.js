import { callMCPTool } from './egx/tv_bridge.js';

export class Client {
  constructor() {
    this.connected = false;
  }

  async connect() {
    const health = await callMCPTool('tv_health_check', {});
    if (!health?.success) {
      throw new Error(health?.error || 'TradingView connection failed');
    }
    this.connected = true;
    return health;
  }

  async disconnect() {
    this.connected = false;
    return { success: true };
  }

  async callTool(name, params = {}) {
    return callMCPTool(name, params);
  }
}

export default Client;

const { MCPServer } = require('@model-context-protocol/sdk');

describe('MCP Server', () => {
  test('should be able to import MCPServer', () => {
    expect(MCPServer).toBeDefined();
  });

  test('should be able to create an instance', () => {
    const server = new MCPServer({
      name: 'test-server',
      version: '1.0.0'
    });
    
    expect(server).toBeDefined();
    expect(server.name).toBe('test-server');
  });
});
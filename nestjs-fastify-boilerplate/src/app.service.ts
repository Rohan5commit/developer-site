import { Injectable } from '@nestjs/common';

@Injectable()
export class AppService {
  private readonly startedAt = new Date();

  getOverview() {
    return {
      name: 'nestjs-fastify-boilerplate',
      description:
        'Fastify-first NestJS starter for health checks and Modal workload planning.',
      version: '0.1.0',
      docs: '/docs-json',
      routes: ['/health', '/api/compute/presets', '/api/compute/plan'],
      startedAt: this.startedAt.toISOString(),
    };
  }

  getHealth() {
    return {
      service: 'nestjs-fastify-boilerplate',
      status: 'ok',
      timestamp: new Date().toISOString(),
      uptimeSeconds: Math.floor((Date.now() - this.startedAt.getTime()) / 1000),
    };
  }
}

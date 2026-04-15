import { Test, TestingModule } from '@nestjs/testing';
import {
  FastifyAdapter,
  NestFastifyApplication,
} from '@nestjs/platform-fastify';
import request from 'supertest';
import { AppModule } from './../src/app.module';
import { configureApp } from './../src/configure-app';

type ComputePlanResponse = {
  iterations: number;
  workers: number;
  salt: number;
  recommendedExecution: string;
  fullChecksumComputed: boolean;
  partitions: unknown[];
};

describe('AppController (e2e)', () => {
  let app: NestFastifyApplication;

  beforeEach(async () => {
    const moduleFixture: TestingModule = await Test.createTestingModule({
      imports: [AppModule],
    }).compile();

    app = moduleFixture.createNestApplication<NestFastifyApplication>(
      new FastifyAdapter(),
    );
    configureApp(app);
    await app.init();
    await app.getHttpAdapter().getInstance().ready();
  });

  afterEach(async () => {
    await app.close();
  });

  it('/health (GET)', async () => {
    const response = await request(app.getHttpServer())
      .get('/health')
      .expect(200);

    expect(response.body).toMatchObject({ status: 'ok' });
  });

  it('/api/compute/plan (POST)', async () => {
    const response = await request(app.getHttpServer())
      .post('/api/compute/plan')
      .send({ iterations: 1200, workers: 3, salt: 17 })
      .expect(200);
    const body = response.body as ComputePlanResponse;

    expect(body).toMatchObject({
      iterations: 1200,
      workers: 3,
      salt: 17,
      recommendedExecution: 'local',
      fullChecksumComputed: true,
    });
    expect(body.partitions).toHaveLength(3);
  });

  it('/api/compute/plan (POST) rejects invalid input', () => {
    return request(app.getHttpServer())
      .post('/api/compute/plan')
      .send({ iterations: 0, workers: 0, salt: -1 })
      .expect(400);
  });
});

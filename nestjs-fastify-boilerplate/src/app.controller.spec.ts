import { Test, TestingModule } from '@nestjs/testing';
import { AppController } from './app.controller';
import { AppService } from './app.service';

describe('AppController', () => {
  let appController: AppController;

  beforeEach(async () => {
    const app: TestingModule = await Test.createTestingModule({
      controllers: [AppController],
      providers: [AppService],
    }).compile();

    appController = app.get<AppController>(AppController);
  });

  describe('root', () => {
    it('should expose overview links', () => {
      const overview = appController.getOverview();

      expect(overview).toMatchObject({
        name: 'nestjs-fastify-boilerplate',
        docs: '/docs-json',
      });
      expect(overview.routes).toEqual(
        expect.arrayContaining([
          '/health',
          '/api/compute/presets',
          '/api/compute/plan',
        ]),
      );
    });
  });
});

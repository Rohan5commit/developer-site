import { ValidationPipe } from '@nestjs/common';
import { NestFastifyApplication } from '@nestjs/platform-fastify';
import { DocumentBuilder, SwaggerModule } from '@nestjs/swagger';

export function configureApp(app: NestFastifyApplication) {
  app.useGlobalPipes(
    new ValidationPipe({
      transform: true,
      whitelist: true,
      forbidNonWhitelisted: true,
    }),
  );
  app.enableShutdownHooks();

  const config = new DocumentBuilder()
    .setTitle('NestJS Fastify Modal Starter')
    .setDescription(
      'Health and compute-planning API for Modal-backed workloads.',
    )
    .setVersion('0.1.0')
    .addTag('meta')
    .addTag('health')
    .addTag('compute')
    .build();
  const document = SwaggerModule.createDocument(app, config);

  app
    .getHttpAdapter()
    .getInstance()
    .get('/docs-json', async (_request, reply) => {
      return reply.send(document);
    });
}

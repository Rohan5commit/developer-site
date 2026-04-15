import { Module } from '@nestjs/common';
import { AppController } from './app.controller';
import { AppService } from './app.service';
import { ComputeController } from './compute.controller';
import { ComputeService } from './compute.service';
import { HealthController } from './health.controller';

@Module({
  imports: [],
  controllers: [AppController, HealthController, ComputeController],
  providers: [AppService, ComputeService],
})
export class AppModule {}

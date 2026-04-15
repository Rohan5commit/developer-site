import { Controller, Get } from '@nestjs/common';
import { ApiOkResponse, ApiTags } from '@nestjs/swagger';
import { AppService } from './app.service';

@ApiTags('health')
@Controller('health')
export class HealthController {
  constructor(private readonly appService: AppService) {}

  @Get()
  @ApiOkResponse({ description: 'Simple health and uptime response.' })
  getHealth() {
    return this.appService.getHealth();
  }
}

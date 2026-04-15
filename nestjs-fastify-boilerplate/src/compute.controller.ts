import { Body, Controller, Get, HttpCode, Post } from '@nestjs/common';
import { ApiOkResponse, ApiTags } from '@nestjs/swagger';
import { ComputeService } from './compute.service';
import { RunComputeDto } from './dto/run-compute.dto';

@ApiTags('compute')
@Controller('api/compute')
export class ComputeController {
  constructor(private readonly computeService: ComputeService) {}

  @Get('presets')
  @ApiOkResponse({
    description: 'Example payloads for local and Modal execution.',
  })
  getPresets() {
    return {
      local: { iterations: 200_000, workers: 2, salt: 17 },
      modal: { iterations: 24_000_000, workers: 6, salt: 23 },
    };
  }

  @Post('plan')
  @HttpCode(200)
  @ApiOkResponse({
    description: 'Normalized workload plan with preview checksum.',
  })
  createPlan(@Body() payload: RunComputeDto) {
    return this.computeService.createPlan(payload);
  }
}

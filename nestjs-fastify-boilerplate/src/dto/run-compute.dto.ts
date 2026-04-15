import { Type } from 'class-transformer';
import { IsInt, IsOptional, Max, Min } from 'class-validator';
import { ApiProperty, ApiPropertyOptional } from '@nestjs/swagger';

export class RunComputeDto {
  @ApiProperty({ example: 24000000, description: 'Total loop iterations.' })
  @Type(() => Number)
  @IsInt()
  @Min(1)
  @Max(100000000)
  iterations!: number;

  @ApiPropertyOptional({
    example: 6,
    default: 4,
    description: 'Requested worker count before normalization.',
  })
  @Type(() => Number)
  @IsOptional()
  @IsInt()
  @Min(1)
  @Max(32)
  workers = 4;

  @ApiPropertyOptional({
    example: 17,
    default: 17,
    description: 'Checksum salt used for preview generation.',
  })
  @Type(() => Number)
  @IsOptional()
  @IsInt()
  @Min(0)
  @Max(2147483647)
  salt = 17;
}

import { Injectable } from '@nestjs/common';
import { RunComputeDto } from './dto/run-compute.dto';

const CHECKSUM_MASK = (1n << 64n) - 1n;
const PREVIEW_ITERATION_LIMIT = 20_000;
const LOCAL_ITERATION_THRESHOLD = 1_000_000;
const ITERATIONS_PER_SECOND_PER_WORKER = 350_000;

@Injectable()
export class ComputeService {
  createPlan(payload: RunComputeDto) {
    const workers = Math.max(1, Math.min(payload.workers, payload.iterations));
    const partitions = this.buildPartitions(payload.iterations, workers);
    const previewIterations = Math.min(
      payload.iterations,
      PREVIEW_ITERATION_LIMIT,
    );

    return {
      iterations: payload.iterations,
      workers,
      salt: payload.salt,
      partitions,
      previewIterations,
      previewChecksum: this.computePreviewChecksum(
        previewIterations,
        workers,
        payload.salt,
      ),
      fullChecksumComputed: previewIterations === payload.iterations,
      recommendedExecution:
        payload.iterations >= LOCAL_ITERATION_THRESHOLD ? 'modal' : 'local',
      estimatedDurationSeconds: Number(
        (
          payload.iterations /
          Math.max(workers, 1) /
          ITERATIONS_PER_SECOND_PER_WORKER
        ).toFixed(2),
      ),
    };
  }

  private buildPartitions(iterations: number, workers: number) {
    const base = Math.floor(iterations / workers);
    const remainder = iterations % workers;
    let offset = 0;

    return Array.from({ length: workers }, (_, index) => {
      const count = base + (index < remainder ? 1 : 0);
      const start = offset;
      offset += count;
      return { worker: index + 1, start, count };
    });
  }

  private computePreviewChecksum(
    iterations: number,
    workers: number,
    salt: number,
  ): string {
    const partitions = this.buildPartitions(iterations, workers);
    let checksum = 0n;

    for (const part of partitions) {
      checksum ^= this.workerChecksum(part.start, part.count, salt);
    }

    return checksum.toString();
  }

  private workerChecksum(start: number, count: number, salt: number): bigint {
    let acc = 0n;
    const saltValue = BigInt(salt);
    const end = BigInt(start + count);

    for (let current = BigInt(start); current < end; current += 1n) {
      acc =
        (acc + ((current * current + saltValue) ^ (current * 2654435761n))) &
        CHECKSUM_MASK;
    }

    return acc;
  }
}

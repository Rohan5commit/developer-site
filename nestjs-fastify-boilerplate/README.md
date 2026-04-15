# NestJS Fastify Modal Starter

A Fastify-first NestJS starter with useful endpoints instead of the stock hello-world route.

## Endpoints

- `GET /`: project metadata and quick links.
- `GET /health`: service status, uptime, and timestamp.
- `GET /api/compute/presets`: sample local and Modal payloads.
- `POST /api/compute/plan`: validated workload planning endpoint.
- `GET /docs-json`: generated OpenAPI JSON.

## Example request

```bash
curl -X POST http://localhost:3000/api/compute/plan   -H 'content-type: application/json'   -d '{"iterations":24000000,"workers":6,"salt":23}'
```

Example response fields:

- normalized worker count
- partitioned work ranges
- preview checksum for the first 20,000 iterations
- local vs Modal execution recommendation
- estimated duration in seconds

## Scripts

```bash
npm ci
npm run lint
npm run build
npm test
npm run test:e2e
npm run start:dev
```

Use `npm run lint:fix` if you want ESLint to apply autofixes locally.

# Context graph providers

The context graph is a dedicated subsystem under `refinery_ai/context_graph/`. Persisted models,
provider contracts, deterministic merging, storage, querying, and repository fingerprinting live in
focused modules. Graphify, Spring, database, Kafka, and Redis providers all live in its `providers/`
package and share only repository-scanning and evidence-construction utilities.

Refinery builds one versioned context graph from independently useful providers. Each provider
emits normalized nodes and edges with file and line provenance. The builder merges matching stable
IDs and records provider status and diagnostics in `.refinery/context/context-graph.json`.
Graphs also record their creation time and a fingerprint of relevant repository inputs so consumers
can identify stale evidence.

## Graphify provider

`GraphifyProvider` supplies structural code evidence such as symbols and relationships. Its native
`graphify-out/graph.json` remains available for diagnostics but is not the application-level graph
contract.

## Spring provider

`SpringProvider` activates when repository discovery identifies Spring dependencies. It currently
recognizes:

- component stereotypes and named beans;
- constructor, `@Autowired`, `@Inject`, and `@Resource` injection;
- controller request mappings and HTTP methods;
- `@Bean`, `@Value`, and `@ConfigurationProperties` configuration;
- JPA entities, table mappings, and Spring Data repository entity types;
- `@KafkaListener` topic consumption; and
- `@Cacheable`, `@CachePut`, and `@CacheEvict` cache access.

Provider output uses stable namespaces such as `spring:bean`, `http:endpoint`, `database:table`,
`kafka:topic`, `redis:cache`, and `configuration:property`. The source implementation is deliberately
deterministic and local. Full Java symbol resolution, composed annotations, XML configuration, and
runtime wiring are not yet inferred.

## Architecture summaries

`refinery analyze REPOSITORY` reads the existing normalized graph (or builds it when absent) and
combines it with live discovery, folder structure, and deterministic assessment metrics. It reports
architectural shape, interfaces, data usage, prominent components, modernization posture, and
provider completeness.
The command retains its assessment and summary artifacts under `.refinery/`.

Use `--json` for the typed summary contract, `--output FILE` for a reusable report, and `--top N` to
control the number of prominent components. A graph from another repository is rejected, while a
fingerprint mismatch is reported as a stale-evidence warning.

## Resource providers

`DatabaseProvider` activates for JDBC, JPA, database driver, Flyway, or Liquibase dependencies. It
records configured database engines and connections, tables and foreign-key references found in SQL
files, and literal SQL table reads and writes made through common JDBC-style calls. Connection
locations are redacted in graph attributes.

`KafkaProvider` activates for Kafka dependencies. It records configured clusters and topic
properties, `@KafkaListener` consumers, and literal topics passed to producer `send` calls.

`RedisProvider` activates for Redis or Redisson dependencies. It records configured Redis instances,
`@RedisHash` keyspaces, and literal keys used through common Redis template operations. Connection
hosts are redacted in graph attributes.

All three providers work independently of Spring and merge their evidence with Spring-derived nodes
when canonical resource IDs match. Dynamically assembled SQL, topic names, and Redis keys are left
unresolved instead of being guessed.

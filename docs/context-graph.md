# Context graph providers

Refinery builds one versioned context graph from independently useful providers. Each provider
emits normalized nodes and edges with file and line provenance. The builder merges matching stable
IDs and records provider status and diagnostics in `.refinery/context/context-graph.json`.

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

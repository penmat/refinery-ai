package example;

class InfrastructureClient {
    private JdbcTemplate jdbc;
    private KafkaTemplate<String, String> kafka;
    private StringRedisTemplate redis;

    void load() {
        jdbc.query("SELECT * FROM orders", mapper);
        kafka.send("orders.created", "event");
        redis.opsForValue().set("sessions", "active");
    }

    @KafkaListener(topics = "orders.received")
    void receive(String event) {
    }
}

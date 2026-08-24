package example;

@RestController
@RequestMapping("/orders")
public class OrderController {
    private final OrderService service;

    public OrderController(OrderService service) {
        this.service = service;
    }

    @PostMapping
    public Order create() {
        return service.create();
    }
}

@Service
class OrderService {
    @Value("${orders.region:us-west}")
    private String region;

    @Cacheable(cacheNames = "orders")
    public Order find() {
        return null;
    }

    @KafkaListener(topics = "orders.created")
    public void consume(String event) {
    }
}

@Entity
@Table(name = "orders")
class Order {
}

@Repository
interface OrderRepository extends JpaRepository<Order, Long> {
}

@ConfigurationProperties(prefix = "orders.client")
class OrderClientProperties {
}

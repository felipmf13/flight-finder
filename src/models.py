from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Segment:
    origin: str
    destination: str
    departure: datetime
    arrival: datetime
    airline: str
    airline_name: str
    flight_number: str
    duration_minutes: int


@dataclass
class Itinerary:
    segments: list

    @property
    def origin(self) -> str:
        return self.segments[0].origin

    @property
    def destination(self) -> str:
        return self.segments[-1].destination

    @property
    def departure(self) -> datetime:
        return self.segments[0].departure

    @property
    def arrival(self) -> datetime:
        return self.segments[-1].arrival

    @property
    def duration_minutes(self) -> int:
        return sum(s.duration_minutes for s in self.segments)

    @property
    def stops(self) -> int:
        return len(self.segments) - 1

    @property
    def airline(self) -> str:
        return self.segments[0].airline

    @property
    def airline_name(self) -> str:
        return self.segments[0].airline_name


@dataclass
class FlightOffer:
    id: str
    provider: str
    outbound: Itinerary
    inbound: Optional[Itinerary]
    price: float                    # Total for all passengers (both legs)
    currency: str
    cabin_class: str
    adults: int
    price_available: bool = True
    outbound_price: float = 0.0     # Per-person outbound leg price
    inbound_price: float = 0.0      # Per-person inbound leg price
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def price_per_person(self) -> float:
        return self.price / self.adults if self.adults > 0 else self.price

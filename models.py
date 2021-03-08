from dataclasses import dataclass


@dataclass
class Bus:
    busId: str
    lat: float
    lng: float
    route: str


@dataclass
class WindowBounds:
    south_lat: float
    north_lat: float
    west_lng: float
    east_lng: float

    def update(self, south_lat, north_lat, west_lng, east_lng):
        self.south_lat = south_lat
        self.north_lat = north_lat
        self.west_lng = west_lng
        self.east_lng = east_lng

    def is_inside(self, lat, lng):
        if self.south_lat < lat < self.north_lat \
           and self.west_lng < lng < self.east_lng:
            return True

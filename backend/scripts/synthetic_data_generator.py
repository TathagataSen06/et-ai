"""Synthetic counterfeit-network data generator (Faker, fully fabricated).

Generates distributors -> dealers -> seizures/scans clustered around real Indian
city coordinates so DBSCAN produces meaningful hotspots. All names, phones, and
account numbers are fake; no real fraud data is involved.
"""
import argparse
import json
import math
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from faker import Faker

CITIES: dict[str, tuple[float, float]] = {
    "Mumbai": (19.0760, 72.8777),
    "Delhi": (28.7041, 77.1025),
    "Bangalore": (12.9716, 77.5946),
    "Hyderabad": (17.3850, 78.4867),
    "Chennai": (13.0827, 80.2707),
    "Pune": (18.5204, 73.8567),
    "Kolkata": (22.5726, 88.3639),
    "Jaipur": (26.9124, 75.7873),
    "Lucknow": (26.8467, 80.9462),
    "Ahmedabad": (23.0225, 72.5714),
    "Surat": (21.1458, 72.8336),
    "Chandigarh": (30.7333, 76.7794),
}

AGENCIES = ["State Police", "RBI", "CBI", "Income Tax", "DRI"]
LOCATION_TYPES = ["ATM", "Bank", "Shop", "Street", "Market", "Transport Hub"]
BANKS = ["HDFC", "ICICI", "Axis", "SBI", "Kotak"]


class CounterfeitNetworkGenerator:
    def __init__(self, seed: int = 42):
        self.fake = Faker("en_IN")
        Faker.seed(seed)
        self.rng = random.Random(seed)

    def _nearby(self, base: tuple[float, float], radius_km: float = 8.0) -> tuple[float, float]:
        lat, lon = base
        lat_off = self.rng.uniform(-radius_km, radius_km) / 111.0
        lon_off = self.rng.uniform(-radius_km, radius_km) / (111.0 * math.cos(math.radians(lat)))
        return (round(lat + lat_off, 6), round(lon + lon_off, 6))

    def generate_network(self, num_seizures: int = 120) -> dict:
        distributors = []
        for _ in range(self.rng.randint(3, 5)):
            city = self.rng.choice(list(CITIES))
            distributors.append({
                "distributor_id": str(uuid.UUID(int=self.rng.getrandbits(128), version=4)),
                "name": self.fake.name(),
                "phone": self.fake.phone_number(),
                "primary_city": city,
                "coordinates": CITIES[city],
                "operation_scale": self.rng.choice(["LOCAL", "REGIONAL", "NATIONAL"]),
            })

        dealers = []
        for distributor in distributors:
            # Dealers cluster in the distributor's city plus one spillover city.
            cities = [distributor["primary_city"], self.rng.choice(list(CITIES))]
            for _ in range(self.rng.randint(4, 8)):
                city = self.rng.choice(cities)
                dealers.append({
                    "dealer_id": str(uuid.UUID(int=self.rng.getrandbits(128), version=4)),
                    "distributor_id": distributor["distributor_id"],
                    "name": self.fake.name(),
                    "phone": self.fake.phone_number(),
                    "city": city,
                    "coordinates": self._nearby(CITIES[city], radius_km=6),
                    "operation_type": self.rng.choice(LOCATION_TYPES),
                    "estimated_monthly_volume": self.rng.randint(10_000, 100_000),
                })

        base_date = datetime.now(timezone.utc) - timedelta(days=90)
        seizures = []
        # Weight dealers so a few become genuine hotspots rather than uniform noise.
        weights = [self.rng.uniform(0.2, 1.0) ** 2 for _ in dealers]
        for _ in range(num_seizures):
            dealer = self.rng.choices(dealers, weights=weights, k=1)[0]
            lat, lon = self._nearby(dealer["coordinates"], radius_km=1.5)
            seizures.append({
                "seizure_id": str(uuid.UUID(int=self.rng.getrandbits(128), version=4)),
                "seizure_date": (base_date + timedelta(
                    days=self.rng.randint(0, 89), hours=self.rng.randint(0, 23)
                )).isoformat(),
                "lat": lat,
                "lon": lon,
                "denomination": str(self.rng.choice([500, 2000])),
                "quantity": self.rng.randint(50, 500),
                "location_type": dealer["operation_type"],
                "linked_dealer_id": dealer["dealer_id"],
                "counterfeit_confidence": round(self.rng.uniform(0.65, 0.99), 2),
                "seized_by_agency": self.rng.choice(AGENCIES),
            })

        accounts = []
        for dealer in self.rng.sample(dealers, max(1, int(len(dealers) * 0.6))):
            bank = self.rng.choice(BANKS)
            accounts.append({
                "account_id": str(uuid.UUID(int=self.rng.getrandbits(128), version=4)),
                "dealer_id": dealer["dealer_id"],
                "bank_name": bank,
                "account_number": str(self.rng.randint(10**11, 10**12 - 1)),
                "ifsc_code": f"{bank[:4].upper()}0{self.rng.randint(100000, 999999)}",
                "total_inflow_inr": self.rng.randint(500_000, 5_000_000),
                "velocity_per_day": self.rng.randint(2, 10),
                "is_verified": self.rng.random() < 0.66,
            })

        return {
            "network_id": str(uuid.uuid4()),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "distributors": distributors,
            "dealers": dealers,
            "seizures": seizures,
            "accounts": accounts,
            "statistics": {
                "total_distributors": len(distributors),
                "total_dealers": len(dealers),
                "total_seizures": len(seizures),
                "total_notes_seized": sum(s["quantity"] for s in seizures),
                "estimated_circulation_inr": sum(
                    s["quantity"] * int(s["denomination"]) for s in seizures
                ),
            },
        }

    # ------------------------------------------------------------------
    # Digital-arrest scam campaigns: numbers rotated across a shared device
    # pool (the linking signal), scripted sessions, and victim reports that
    # mention the numbers so campaign clustering has real cross-references.
    # ------------------------------------------------------------------

    _SCAM_SCRIPTS: dict[str, list[str]] = {
        "DIGITAL_ARREST_CBI": [
            "Hello, I am calling from CBI headquarters Delhi, officer badge number {badge}.",
            "A case has been registered against your Aadhaar for money laundering of 2 crore rupees.",
            "An arrest warrant is issued. This is a digital arrest — stay on the video call and keep your camera on.",
            "Do not tell your family or your bank, this is strictly confidential under the Official Secrets Act.",
            "You are transferred to my senior officer for the Skype video statement.",
            "To verify your funds you must transfer your savings by RTGS to the safe custody account. It is a refundable security deposit.",
        ],
        "PARCEL_CUSTOMS_NCB": [
            "This is Mumbai customs office regarding a parcel booked on your name to Taiwan.",
            "The courier was seized — drugs found inside, 400 grams MDMA, along with five passports.",
            "A case is filed with NCB narcotics division. Your Aadhaar was used in the booking.",
            "Do not disclose this call to anyone, you are under surveillance until the verification completes.",
            "We are connecting you to the NCB officer for your video statement, keep the call connected.",
            "Pay the legalization fee now by NEFT or the arrest warrant will be executed today.",
        ],
        "TRAI_SIM_DEACTIVATION": [
            "This is an urgent call from the telecom department, TRAI regulatory wing.",
            "Your SIM will be deactivated within 2 hours because your number is linked to illegal activity.",
            "A complaint is registered against your number in Mumbai cyber cell.",
            "Press 9 to speak to the police officer handling your case.",
            "Stay on the line, do not disconnect, your statement is being recorded.",
        ],
        "BANK_KYC_ED": [
            "I am calling from your bank head office, your KYC is expired and the account will be frozen today.",
            "The Enforcement Directorate has flagged suspicious transactions found in your account.",
            "To keep the account active, share the OTP you just received for re-verification.",
            "Do not inform the branch, the verification must be completed on this call only.",
            "Transfer the balance to the temporary government verification account until the audit completes.",
        ],
    }

    def generate_scam_campaigns(self, accounts: list[dict], num_campaigns: int = 3) -> dict:
        families = list(self._SCAM_SCRIPTS)
        mule_pool = sorted(
            accounts, key=lambda a: (a["is_verified"], -a["velocity_per_day"])
        )[: max(num_campaigns * 2, 4)]
        foreign_prefixes = ["+92 3", "+855 9", "+84 9"]
        now = datetime.now(timezone.utc)

        campaigns, sessions, victim_reports = [], [], []
        for c in range(num_campaigns):
            family = families[c % len(families)]
            numbers = []
            for _ in range(self.rng.randint(2, 3)):
                if self.rng.random() < 0.6:
                    prefix = self.rng.choice(foreign_prefixes)
                    numbers.append(f"{prefix}{self.rng.randint(10_000_000, 99_999_999)}")
                else:
                    numbers.append(f"+91 {self.rng.randint(6, 9)}{self.rng.randint(100_000_000, 999_999_999)}")
            devices = [f"{self.rng.getrandbits(128):032x}" for _ in range(2)]
            mules = [m["account_id"] for m in self.rng.sample(mule_pool, k=min(2, len(mule_pool)))]

            campaign_sessions = []
            for i in range(self.rng.randint(4, 6)):
                lines = self._SCAM_SCRIPTS[family]
                # Most sessions run the full script; a couple stall early
                # (victim hung up) so risk scores vary realistically.
                cut = len(lines) if self.rng.random() < 0.7 else self.rng.randint(2, 3)
                transcript = " ".join(lines[:cut]).format(badge=self.rng.randint(1000, 9999))
                session = {
                    "caller_number": self.rng.choice(numbers),
                    "victim_contact": f"+91 {self.rng.randint(6, 9)}{self.rng.randint(100_000_000, 999_999_999)}",
                    "channel": self.rng.choice(["VOICE", "VIDEO", "WHATSAPP"]),
                    "duration_minutes": round(self.rng.uniform(18, 190), 1),
                    "device_hash": self.rng.choice(devices),
                    "mule_account_id": self.rng.choice(mules) if cut == len(lines) else None,
                    "transcript": transcript,
                    "created_at": (now - timedelta(days=self.rng.uniform(0.2, 21),
                                                   hours=self.rng.uniform(0, 12))).isoformat(),
                }
                sessions.append(session)
                campaign_sessions.append(session)

            campaigns.append({
                "script_family": family,
                "caller_numbers": numbers,
                "device_hashes": devices,
                "mule_account_ids": mules,
                "session_count": len(campaign_sessions),
            })

            for _ in range(self.rng.randint(2, 3)):
                city = self.rng.choice(list(CITIES))
                lat, lon = self._nearby(CITIES[city], radius_km=12)
                number = self.rng.choice(numbers)
                victim_reports.append({
                    "report_id": str(uuid.UUID(int=self.rng.getrandbits(128), version=4)),
                    "channel": self.rng.choice(["WEB", "WHATSAPP"]),
                    "lat": lat,
                    "lon": lon,
                    "description": self.rng.choice([
                        f"Got a call from {number} claiming to be {family.split('_')[-1]} officers, "
                        "they said I am under digital arrest and demanded money transfer.",
                        f"Number {number} kept me on video call for hours saying a parcel with drugs "
                        "was booked in my name. They asked for a security deposit.",
                        f"Received threatening call from {number}, caller knew my Aadhaar details "
                        "and demanded RTGS transfer to avoid arrest.",
                    ]),
                    "created_at": (now - timedelta(days=self.rng.uniform(0.1, 18))).isoformat(),
                })

        return {"campaigns": campaigns, "sessions": sessions, "victim_reports": victim_reports}

    def generate_scan_records(self, num_scans: int = 300) -> list[dict]:
        base_date = datetime.now(timezone.utc) - timedelta(days=30)
        scans = []
        for _ in range(num_scans):
            city = self.rng.choice(list(CITIES))
            lat, lon = self._nearby(CITIES[city], radius_km=15)
            score = round(self.rng.betavariate(2, 5), 3)  # most scans are genuine
            recommendation = (
                "LIKELY_COUNTERFEIT" if score > 0.75
                else "SUSPICIOUS" if score > 0.50
                else "LIKELY_GENUINE"
            )
            scans.append({
                "scan_id": str(uuid.UUID(int=self.rng.getrandbits(128), version=4)),
                "user_type": self.rng.choice(["Merchant", "Citizen", "Bank Teller"]),
                "lat": lat,
                "lon": lon,
                "denomination": str(self.rng.choice([100, 200, 500, 2000])),
                "counterfeit_score": score,
                "recommendation": recommendation,
                "timestamp": (base_date + timedelta(hours=self.rng.randint(0, 719))).isoformat(),
                "city": city,
            })
        return scans


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic Netra data")
    parser.add_argument("--seizures", type=int, default=120)
    parser.add_argument("--scans", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out-dir", type=Path, default=Path(__file__).parent / "data")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    generator = CounterfeitNetworkGenerator(seed=args.seed)

    network = generator.generate_network(num_seizures=args.seizures)
    network["scam"] = generator.generate_scam_campaigns(network["accounts"])
    (args.out_dir / "synthetic_network.json").write_text(json.dumps(network, indent=2))

    scans = generator.generate_scan_records(num_scans=args.scans)
    (args.out_dir / "synthetic_scans.json").write_text(json.dumps(scans, indent=2))

    print(f"Generated {len(network['dealers'])} dealers, {len(network['seizures'])} seizures, "
          f"{len(scans)} scans, {len(network['scam']['sessions'])} scam sessions "
          f"in {len(network['scam']['campaigns'])} campaigns -> {args.out_dir}")


if __name__ == "__main__":
    main()

from fastapi import APIRouter

from ..routes import auth, bills, contractors, dashboard, diesel, financial_entities, ledger, materials, orders, petrol_pumps, plants, rates, reports, settings, sites, transactions, vehicle_owners, vehicles


api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(dashboard.router)
api_router.include_router(diesel.router)
api_router.include_router(ledger.router)
api_router.include_router(orders.router)
api_router.include_router(bills.router)
api_router.include_router(contractors.router)
api_router.include_router(materials.router)
api_router.include_router(petrol_pumps.router)
api_router.include_router(vehicle_owners.router)
api_router.include_router(vehicles.router)
api_router.include_router(plants.router)
api_router.include_router(rates.router)
api_router.include_router(sites.router)
api_router.include_router(reports.router)
api_router.include_router(transactions.router)
api_router.include_router(financial_entities.router)
api_router.include_router(settings.router)

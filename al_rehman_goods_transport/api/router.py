from fastapi import APIRouter

from ..routes import auth, bills, contractors, dashboard, diesel, financial_entities, ledger, materials, orders, petrol_pumps, plants, rates, reports, settings, sites, transactions, vehicle_owners, vehicles


# Order preserved from the original explicit include_router sequence.
_MODULES = [
    auth, dashboard, diesel, ledger, orders, bills, contractors, materials,
    petrol_pumps, vehicle_owners, vehicles, plants, rates, sites, reports,
    transactions, financial_entities, settings,
]

api_router = APIRouter()
for _module in _MODULES:
    api_router.include_router(_module.router)

# Flat list of every sub-route object. Each module's router keeps its .routes
# flat (populated directly by the @router decorators), so this is reliable for
# name->path resolution across Starlette versions — unlike app.router.routes,
# which nests included routers inside an _IncludedRouter in Starlette 1.x.
api_route_objects = [route for _module in _MODULES for route in _module.router.routes]

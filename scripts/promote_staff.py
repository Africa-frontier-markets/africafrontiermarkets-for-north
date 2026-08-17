"""
Promotion d'un utilisateur au rôle AFM_STAFF.

Volontairement un script d'exécution directe (accès serveur/infra requis),
pas un endpoint API — même à un utilisateur déjà authentifié, exposer une
route de promotion de rôle créerait un vecteur d'élévation de privilège
trivial à protéger (qui peut promouvoir qui ?). Ce script suppose que
l'exécutant a déjà un accès de confiance à l'infrastructure (VM/conteneur de
production), ce qui est le bon niveau de contrôle pour une opération aussi
sensible tant qu'aucun processus d'approbation formel n'existe.

Usage :
    python -m scripts.promote_staff operateur@africafrontiermarkets.com
    python -m scripts.promote_staff operateur@africafrontiermarkets.com --revoke
"""

import argparse
import asyncio
import sys

from sqlalchemy import select

from config.database import AsyncSessionLocal
from payment_hub.models import User, UserRole


async def promote(email: str, revoke: bool = False) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if not user:
            print(f"Aucun utilisateur trouvé pour {email}", file=sys.stderr)
            sys.exit(1)

        user.role = UserRole.MERCHANT if revoke else UserRole.AFM_STAFF
        await session.commit()

        action = "rétrogradé en MERCHANT" if revoke else "promu AFM_STAFF"
        print(f"{email} {action}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email", help="E-mail de l'utilisateur à promouvoir/rétrograder")
    parser.add_argument("--revoke", action="store_true", help="Rétrograde en MERCHANT au lieu de promouvoir")
    args = parser.parse_args()

    asyncio.run(promote(args.email, revoke=args.revoke))

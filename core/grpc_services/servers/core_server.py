async def start_grpc_server():
    print("   🔌 gRPC Server: Démarré (Port 50051 - Simulation)")
    # Simulation d'une tâche longue pour garder le serveur en vie
    import asyncio
    while True:
        await asyncio.sleep(3600)
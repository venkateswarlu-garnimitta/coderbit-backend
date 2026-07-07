from fastapi import HTTPException

PORT_RANGE = range(9000, 9100)


def acquire_port(used_ports: set[int]) -> int:
    for port in PORT_RANGE:
        if port not in used_ports:
            return port
    raise HTTPException(status_code=503, detail="No ports available")

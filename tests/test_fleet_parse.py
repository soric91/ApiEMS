"""Cómo se lee el árbol que devuelve CRMBackend.

Es la traducción entre dos formas: un árbol anidado de cuatro niveles, y la
lista de equipos que usa todo lo demás. Un error acá no se ve como un error
—se ve como un cliente al que le faltan medidores— así que conviene fijarlo.
"""

from typing import Any

from app.services.crm.fleet import walk_devices


def _arbol(*sedes: dict[str, Any], puede_ver: bool = True) -> dict[str, Any]:
    return {"items": [{"puede_ver_consumo": puede_ver, "sites": list(sedes)}], "total": 1}


def _sede(nombre: str, *gateways: dict[str, Any], id: str = "s1") -> dict[str, Any]:
    return {"id": id, "nombre": nombre, "gateways": list(gateways)}


def _gateway(
    serie: str, *equipos: dict[str, Any], estado: str = "online", id: str = "g1"
) -> dict[str, Any]:
    return {"id": id, "numero_serie": serie, "estado": estado, "equipment": list(equipos)}


def _equipo(nombre: str, id: str = "e1", modbus_id: int = 10) -> dict[str, Any]:
    return {"id": id, "nombre_dispositivo": nombre, "modbus_id": modbus_id, "variables": []}


class TestLaJerarquiaSobrevive:
    def test_cada_equipo_conserva_su_sede_y_su_gateway(self) -> None:
        equipos, _, _ = walk_devices(
            _arbol(_sede("Planta Norte", _gateway("GW-0001", _equipo("Medidor 1"))))
        )

        assert len(equipos) == 1
        assert equipos[0].sede == "Planta Norte"
        assert equipos[0].gateway == "GW-0001"
        assert equipos[0].modbus_id == 10

    def test_dos_gateways_de_la_misma_sede_no_se_confunden(self) -> None:
        equipos, _, _ = walk_devices(
            _arbol(
                _sede(
                    "Planta Norte",
                    _gateway("GW-0001", _equipo("A", id="a"), id="g1"),
                    _gateway("GW-0002", _equipo("B", id="b"), id="g2"),
                )
            )
        )

        por_id = {e.id: e for e in equipos}
        assert por_id["a"].gateway == "GW-0001"
        assert por_id["b"].gateway == "GW-0002"


class TestUnGatewayCaido:
    def test_sus_equipos_no_se_pierden(self) -> None:
        """Existen y su histórico está guardado: esconderlos sería perder datos.

        Es exactamente lo que hacía la fuente anterior —el estado en memoria—
        y por lo que este camino existe.
        """
        equipos, _, _ = walk_devices(
            _arbol(
                _sede("Planta Norte", _gateway("GW-0001", _equipo("Medidor"), estado="offline"))
            )
        )

        assert len(equipos) == 1
        assert equipos[0].gateway_en_linea is False

    def test_solo_online_cuenta_como_en_linea(self) -> None:
        """Cualquier otro valor se trata como caído.

        Es la dirección segura del error: decir "no está reportando" cuando sí
        lo hace se corrige mirando; decir que está en línea cuando no, deja a
        alguien buscando datos que no van a llegar.
        """
        equipos, _, _ = walk_devices(
            _arbol(_sede("S", _gateway("GW", _equipo("M"), estado="degradado")))
        )

        assert equipos[0].gateway_en_linea is False


class TestLoQueLlegaRoto:
    def test_un_nivel_que_no_se_pidio_no_rompe(self) -> None:
        """El CRM devuelve `null` para un nivel no solicitado, no `[]`."""
        equipos, _, _ = walk_devices(
            {"items": [{"puede_ver_consumo": True, "sites": None}], "total": 1}
        )

        assert equipos == []

    def test_un_equipo_sin_id_se_descarta(self) -> None:
        """Sin id no se puede ni consultar ni recortar: mostrarlo sería ofrecer
        un medidor que no responde a nada."""
        equipos, _, _ = walk_devices(
            _arbol(_sede("S", _gateway("GW", {"nombre_dispositivo": "Huérfano"})))
        )

        assert equipos == []

    def test_un_equipo_sin_nombre_muestra_su_id(self) -> None:
        equipos, _, _ = walk_devices(
            _arbol(_sede("S", _gateway("GW", {"id": "e9", "variables": []})))
        )

        assert equipos[0].nombre == "e9"

    def test_una_sede_sin_nombre_no_deja_el_grupo_en_blanco(self) -> None:
        equipos, _, _ = walk_devices(
            _arbol({"id": "s1", "gateways": [_gateway("GW", _equipo("M"))]})
        )

        assert equipos[0].sede == "Sin sede"

    def test_un_modbus_id_que_no_es_entero_queda_vacio(self) -> None:
        """Antes que inventar un número: el bus lo usa para direccionar, y un
        valor equivocado apuntaría a otro equipo."""
        equipos, _, _ = walk_devices(
            _arbol(
                _sede(
                    "S",
                    _gateway("GW", {"id": "e1", "modbus_id": "diez", "variables": []}),
                )
            )
        )

        assert equipos[0].modbus_id is None

import pytest

from eor_control.domain import DataQuality
from eor_control.simulators import (
    SimulatedDataAcquisition,
    SimulatedPump,
    SimulatedPumpFault,
    SimulatedPumpState,
    SimulatedValveActuator,
    SimulationDelay,
    VirtualSimulationClock,
)


def test_virtual_pump_models_state_pressure_and_consumed_volume() -> None:
    clock = VirtualSimulationClock()
    pump = SimulatedPump(
        remaining_volume_ml=10.0,
        pressure_ramp_bar_per_second=2.0,
        clock=clock,
    )
    pump.connect()
    pump.enter_remote()
    pump.set_constant_flow(3600.0)
    pump.run()

    clock.advance(2.0)
    status = pump.read_status()

    assert pump.state is SimulatedPumpState.RUNNING
    assert status.pressure_bar == pytest.approx(4.0)
    assert status.remaining_volume_ml == pytest.approx(8.0)


def test_simulated_pump_rejects_run_without_remote_configuration() -> None:
    pump = SimulatedPump()
    pump.connect()

    with pytest.raises(RuntimeError, match="configured"):
        pump.run()


def test_simulated_pump_own_overpressure_limit_stops_without_pc() -> None:
    clock = VirtualSimulationClock()
    pump = SimulatedPump(
        flow_ml_per_hour=100.0,
        pressure_ramp_bar_per_second=10.0,
        hardware_pressure_limit_bar=5.0,
        clock=clock,
    )
    pump.connect()
    pump.start_simulation()

    clock.advance(1.0)
    status = pump.read_status()

    assert status.pressure_bar == pytest.approx(5.0)
    assert pump.state is SimulatedPumpState.FAULT
    assert pump.stop_requested
    assert pump.fault_reason == "OVERPRESSURE"


def test_pressure_stale_fault_is_field_quality_not_connection_loss() -> None:
    pump = SimulatedPump(pressure_bar=20.0)
    pump.connect()
    pump.inject_fault(SimulatedPumpFault.PRESSURE_STALE)

    status, quality = pump.read_cached_status()

    assert status.connected
    assert status.pressure_bar == pytest.approx(20.0)
    assert quality is DataQuality.STALE


def test_clearing_simulated_fault_restores_safe_baseline_and_connection() -> None:
    pump = SimulatedPump(pressure_bar=20.0, remaining_volume_ml=100.0)
    pump.connect()
    pump.inject_fault(SimulatedPumpFault.OVERPRESSURE)
    pump.inject_fault(SimulatedPumpFault.DISCONNECT)

    pump.clear_faults()
    status, quality = pump.read_cached_status()

    assert status.connected
    assert status.pressure_bar == pytest.approx(20.0)
    assert status.remaining_volume_ml == pytest.approx(100.0)
    assert quality is DataQuality.GOOD


def test_virtual_response_delay_is_deterministic_and_does_not_sleep() -> None:
    clock = VirtualSimulationClock()
    pump = SimulatedPump(
        response_delay=SimulationDelay(0.5, 0.5),
        clock=clock,
    )

    pump.connect()
    pump.read_status()

    assert clock.monotonic() == pytest.approx(1.0)


def test_simulated_daq_supports_freeze_spike_and_disconnect() -> None:
    daq = SimulatedDataAcquisition()
    daq.inputs["line"] = 2.0
    daq.freeze("line")
    daq.inputs["line"] = 3.0
    assert daq.read_voltage("line") == pytest.approx(2.0)

    daq.unfreeze("line")
    daq.inject_spike("line", 4.0)
    assert daq.read_voltage("line") == pytest.approx(7.0)
    assert daq.read_voltage("line") == pytest.approx(3.0)

    daq.disconnect()
    with pytest.raises(ConnectionError, match="disconnected"):
        daq.read_voltage("line")


def test_simulated_valve_moves_over_time_and_can_be_stuck() -> None:
    clock = VirtualSimulationClock()
    valve = SimulatedValveActuator(
        maximum_speed_percent_per_second=10.0,
        clock=clock,
    )
    valve.write_percent(50.0)
    clock.advance(2.0)
    assert valve.actual_position() == pytest.approx(20.0)

    valve.stuck = True
    clock.advance(5.0)
    assert valve.actual_position() == pytest.approx(20.0)

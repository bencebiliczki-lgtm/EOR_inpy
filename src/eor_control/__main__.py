from eor_control.calibration import LinearCalibration
from eor_control.simulators import SimulatedDataAcquisition, SimulatedPump


def main() -> None:
    jacket_pump = SimulatedPump(pressure_bar=120.0)
    injection_pump = SimulatedPump(pressure_bar=100.0, flow_ml_per_hour=10.0)
    daq = SimulatedDataAcquisition()
    daq.inputs["line_pressure"] = 2.0
    calibration = LinearCalibration(1.0, 5.0, 0.0, 400.0)

    jacket_pump.connect()
    injection_pump.connect()
    line_pressure = calibration.convert(daq.read_voltage("line_pressure"))
    print("AFKI EOR szimulátor")
    print(f"Köpenypumpa: {jacket_pump.read_status().pressure_bar:.1f} bar")
    print(f"Besajtoló pumpa: {injection_pump.read_status().pressure_bar:.1f} bar")
    print(f"Vonali nyomás: {line_pressure:.1f} bar")


if __name__ == "__main__":
    main()


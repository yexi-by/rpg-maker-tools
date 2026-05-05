//! Python 扩展入口。
//!
//! 本模块只暴露 PyO3 绑定，CPU 密集型规则计算集中放在 `native_core`。

mod native_core;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

#[pyfunction]
fn native_thread_count() -> usize {
    native_core::read_configured_thread_count().unwrap_or_else(rayon::current_num_threads)
}

#[pyfunction]
fn scan_quality(py: Python<'_>, payload_json: String) -> PyResult<String> {
    let result = py.detach(move || {
        native_core::scan_quality_impl(&payload_json).map_err(|error| error.to_string())
    });
    result.map_err(PyValueError::new_err)
}

#[pyfunction]
fn scan_write_protocol(py: Python<'_>, payload_json: String) -> PyResult<String> {
    let result = py.detach(move || {
        native_core::scan_write_protocol_impl(&payload_json).map_err(|error| error.to_string())
    });
    result.map_err(PyValueError::new_err)
}

#[pyfunction]
fn collect_note_tag_sources(py: Python<'_>, payload_json: String) -> PyResult<String> {
    let result = py.detach(move || {
        native_core::collect_note_tag_sources_impl(&payload_json).map_err(|error| error.to_string())
    });
    result.map_err(PyValueError::new_err)
}

#[pyfunction]
fn scan_font_replacements(py: Python<'_>, payload_json: String) -> PyResult<String> {
    let result = py.detach(move || {
        native_core::scan_font_replacements_impl(&payload_json).map_err(|error| error.to_string())
    });
    result.map_err(PyValueError::new_err)
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(native_thread_count, m)?)?;
    m.add_function(wrap_pyfunction!(scan_quality, m)?)?;
    m.add_function(wrap_pyfunction!(scan_write_protocol, m)?)?;
    m.add_function(wrap_pyfunction!(collect_note_tag_sources, m)?)?;
    m.add_function(wrap_pyfunction!(scan_font_replacements, m)?)?;
    Ok(())
}

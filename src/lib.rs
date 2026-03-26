use pyo3::prelude::*;

mod parse;
mod unparse;

/// Exception raised when an item_callback returns a falsy value in streaming mode.
/// Mirrors the ParsingInterrupted class defined in _pure.py.
pyo3::create_exception!(_xmltodict_rs, ParsingInterrupted, pyo3::exceptions::PyException);

#[pymodule]
fn _xmltodict_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse::parse, m)?)?;
    m.add_function(wrap_pyfunction!(unparse::unparse, m)?)?;
    m.add("ParsingInterrupted", m.py().get_type::<ParsingInterrupted>())?;
    Ok(())
}

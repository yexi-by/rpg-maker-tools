//! Rayon 线程池配置。
//!
//! 本模块负责读取 Rust 原生核心线程数配置，并在需要时使用局部线程池执行并发任务。

use std::env;

pub(crate) fn run_with_optional_pool<F, R>(job: F) -> R
where
    F: FnOnce() -> R + Send,
    R: Send,
{
    if let Some(thread_count) = read_configured_thread_count() {
        let pool = match rayon::ThreadPoolBuilder::new()
            .num_threads(thread_count)
            .build()
        {
            Ok(pool) => pool,
            Err(error) => panic!("Rust 线程池创建失败: {error}"),
        };
        return pool.install(job);
    }
    job()
}

pub(crate) fn read_configured_thread_count() -> Option<usize> {
    let raw_value = env::var("ATT_MZ_RUST_THREADS").ok()?;
    parse_configured_thread_count(&raw_value)
}

pub(crate) fn parse_configured_thread_count(raw_value: &str) -> Option<usize> {
    let parsed = raw_value.trim().parse::<usize>().ok()?;
    if parsed == 0 {
        return None;
    }
    Some(parsed)
}

#[cfg(test)]
mod tests {
    use super::parse_configured_thread_count;

    #[test]
    fn thread_count_env_value_controls_configured_pool_size() {
        assert_eq!(parse_configured_thread_count("4"), Some(4));
        assert_eq!(parse_configured_thread_count(" 2 "), Some(2));
        assert_eq!(parse_configured_thread_count("0"), None);
        assert_eq!(parse_configured_thread_count("invalid"), None);
    }
}

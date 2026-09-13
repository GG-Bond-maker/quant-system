import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import ErrorBoundary from './components/ErrorBoundary';
import './index.css';

// 注意：刻意不包 <React.StrictMode> —— 它会把 useEffect 执行两次，
// 导致 Lightweight Charts / ECharts 的 canvas 双重创建与泄漏（详见项目文档难点八）。
// ErrorBoundary 包在应用根：任一页面渲染异常时降级到提示面板，而非整站白屏。
ReactDOM.createRoot(document.getElementById('root')!).render(
  <BrowserRouter>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </BrowserRouter>,
);

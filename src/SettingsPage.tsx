import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { ArrowUpRight, CircleCheck, Eye, EyeOff, KeyRound, Languages, LoaderCircle, LockKeyhole, Save, Server } from 'lucide-react';
import { api } from './api';
import { Empty, Loading } from './components';
import type { TranslationSettings, User } from './types';
import './detail.css';

function ManagedTranslation({settings, user, onLogin}: {settings: TranslationSettings; user: User|null; onLogin: () => void}) {
  return <section className="managed-translation" aria-labelledby="managed-translation-title">
    <div className="settings-section-heading">
      <span className="settings-section-icon"><Languages size={22} /></span>
      <div><h2 id="managed-translation-title">平台翻译服务</h2></div>
      <span className={`connection-status ${settings.configured ? 'configured' : ''}`}><span />{settings.configured ? '已配置' : '未配置'}</span>
    </div>
    <dl className="managed-service-summary">
      <div><dt>翻译引擎</dt><dd>{settings.provider}</dd></div>
      <div><dt>模型</dt><dd className="mono">{settings.model}</dd></div>
    </dl>
    {!user && <div className="managed-access"><button type="button" className="button secondary small" onClick={onLogin}>登录使用翻译<ArrowUpRight size={14} /></button></div>}
  </section>;
}

export default function SettingsPage({user, onLogin, notify}: {user: User|null; onLogin: () => void; notify: (text: string) => void}) {
  const [settings, setSettings] = useState<TranslationSettings|null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [key, setKey] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {api<TranslationSettings>('/settings/translation').then(setSettings).catch(e => setError(e.message)).finally(() => setLoaded(true));}, []);
  const admin = user?.role === 'admin';
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!settings || settings.managed || !admin) return;
    setBusy(true); setError('');
    try {const updated = await api<TranslationSettings>('/settings/translation', {method: 'PUT', body: JSON.stringify({...settings, api_key: key || undefined})}); setSettings(updated); setKey(''); notify('翻译配置已保存');}
    catch (e) {setError((e as Error).message);} finally {setBusy(false);}
  }
  return <div className="page-content settings-page"><div className="page-heading"><h1>工作区设置</h1></div>{!loaded ? <Loading /> : !settings ? <Empty title="无法读取配置" description={error} /> : <div className={`settings-layout ${settings.managed ? 'managed-settings' : ''}`}>{settings.managed ? <ManagedTranslation settings={settings} user={user} onLogin={onLogin} /> : <form className="settings-form" onSubmit={save}><div className="settings-section-heading"><span className="settings-section-icon"><Languages size={22} /></span><div><h2>翻译服务</h2></div><span className={`connection-status ${settings.configured ? 'configured' : ''}`}><span />{settings.configured ? '已配置' : '未配置'}</span></div>{!admin && <div className="settings-access"><LockKeyhole size={17} /><span>{user ? '仅工作区管理员可以修改翻译配置。' : '登录管理员账号以配置翻译服务。'}</span>{!user && <button type="button" className="text-button" onClick={onLogin}>登录<ArrowUpRight size={12} /></button>}</div>}<div className="form-stack"><label>服务提供商</label><div className="provider-options"><button type="button" disabled={!admin} className={settings.provider.toLowerCase() === 'deepseek' ? 'selected' : ''} onClick={() => setSettings({...settings, provider: 'DeepSeek', base_url: 'https://api.deepseek.com', model: 'deepseek-v4-flash'})}><span className="provider-logo">D</span><div><strong>DeepSeek</strong><small>DeepSeek 官方 API</small></div>{settings.provider.toLowerCase() === 'deepseek' && <CircleCheck size={17} />}</button><button type="button" disabled={!admin} className={settings.provider.toLowerCase() !== 'deepseek' ? 'selected' : ''} onClick={() => setSettings({...settings, provider: 'Custom', base_url: '', model: ''})}><span className="provider-logo custom"><Server size={21} /></span><div><strong>自定义服务</strong><small>兼容 Chat Completions</small></div>{settings.provider.toLowerCase() !== 'deepseek' && <CircleCheck size={17} />}</button></div><label>API 地址<input type="url" value={settings.base_url} disabled={!admin} required onChange={e => setSettings({...settings, base_url: e.target.value})} placeholder="https://api.deepseek.com" /><span className="field-help">填写 API 基础地址，服务端会添加 /chat/completions。</span></label><label>模型名称<input value={settings.model} required disabled={!admin} onChange={e => setSettings({...settings, model: e.target.value})} placeholder="deepseek-v4-flash" /></label><label>API Key<div className="key-input"><KeyRound size={15} /><input type={showKey ? 'text' : 'password'} value={key} autoComplete="off" spellCheck={false} disabled={!admin} onChange={e => setKey(e.target.value)} placeholder={settings.configured ? '已配置 · 留空保留现有密钥' : '输入 API Key'} /><button className="icon-button" type="button" disabled={!admin} onClick={() => setShowKey(!showKey)} aria-label={showKey ? '隐藏密钥' : '显示密钥'}>{showKey ? <EyeOff size={16} /> : <Eye size={16} />}</button></div><span className="field-help">密钥仅保存于服务端，不会返回给浏览器。</span></label>{error && <p className="form-error" role="alert">{error}</p>}<div className="settings-save"><button className="button primary" disabled={!admin || busy}>{busy ? <LoaderCircle size={15} className="spin" /> : <Save size={15} />}保存配置</button></div></div></form>}</div>}</div>;
}

# Ubuntu 服务器部署

推荐方式：代码放到 GitHub，服务器通过 `git clone` 或 `git pull` 更新，然后运行部署脚本。应用会由 `systemd` 托管，默认监听 `8501` 端口，SQLite 数据库放在 `/var/lib/stock-helper/stock_helper.db`。

## 1. 本地推送到 GitHub

在本地项目目录执行：

```bash
git init
git add .
git commit -m "Initial stock helper MVP"
git branch -M main
git remote add origin git@github.com:你的用户名/stock-helper.git
git push -u origin main
```

如果你用 HTTPS，把最后两行换成：

```bash
git remote add origin https://github.com/你的用户名/stock-helper.git
git push -u origin main
```

## 2. 服务器首次部署

登录服务器：

```bash
ssh root@你的服务器IP
```

安装 Git，并拉取代码：

```bash
apt-get update
apt-get install -y git
mkdir -p /opt
cd /opt
git clone https://github.com/你的用户名/stock-helper.git
cd /opt/stock-helper
```

运行部署脚本：

```bash
sudo bash scripts/deploy_ubuntu.sh
```

部署完成后访问：

```text
http://你的服务器IP:8501
```

如果云厂商有安全组/防火墙，需要放行 TCP `8501`。

## 3. 日常更新

本地改完代码后推送：

```bash
git add .
git commit -m "Update stock helper"
git push
```

服务器更新：

```bash
cd /opt/stock-helper
git pull
sudo bash scripts/deploy_ubuntu.sh
```

## 4. 常用运维命令

查看状态：

```bash
sudo systemctl status stock-helper
```

查看日志：

```bash
sudo journalctl -u stock-helper -f
```

重启：

```bash
sudo systemctl restart stock-helper
```

停止：

```bash
sudo systemctl stop stock-helper
```

## 5. 自定义端口或路径

部署脚本支持环境变量：

```bash
sudo PORT=8600 bash scripts/deploy_ubuntu.sh
sudo APP_DIR=/opt/stock-helper PORT=8501 bash scripts/deploy_ubuntu.sh
sudo DB_PATH=/data/stock_helper.db bash scripts/deploy_ubuntu.sh
```


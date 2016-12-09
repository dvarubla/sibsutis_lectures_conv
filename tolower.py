import sys
import os
import html5lib
import lxml.etree
from multiprocessing import Process, Pipe, Lock, cpu_count
import json
import re

'''
Скрипт:
переименовывает все файлы и директории именами в нижнем регистре,
заменяет соответственно все href и src в html файлах
удаляет <base target="active"> из файла menu.htm
перекодирует в UTF-8
перекодирует символы из устаревшего шрифта Symbol в юникод

Скрипт работает в несколько потоков
Один поток ioproc — переименовывает файлы,
один — работает при запуске, рекурсивно проходит по файлам,
помещает их в конвейер pipe.
остальные -- функция htmlproc
(их число — второй аргумент скрипта) преобразуют html
'''

# переименовать файл
def rename_to_lower(dirname, basename):
	if not basename.islower():
		print("Renaming "+os.path.join(dirname,basename))
		os.rename(
			os.path.join(dirname,basename),
			os.path.join(dirname,basename.lower())
		)

# заменить html в файле
def replace_to_lower(dirname, basename, replobj):
	fname=os.path.join(dirname, basename)
	print("Replacing in file: "+fname)
	attrs=['href', 'src']

	with open(fname, "rb") as f:
		root=html5lib.parse(
			f,
			treebuilder="lxml",
			namespaceHTMLElements=False,
			default_encoding="Windows-1251"
		)
	# перевод в нижний регистр
	for el in root.xpath("//*[@"+"|@".join(attrs)+"]"):
		for attr in attrs:
			if attr in el.attrib:
				el.set(attr, el.get(attr).lower().replace("\\", "/"))

	# исправление меню
	if basename.startswith("menu."):
		menu=root.xpath("(//base[@target='active'])[1]")
		if len(menu):
			print("Fixing menu")
			menu[0].getparent().remove(menu[0])

	# избавиться от шрифта Symbol
	for el in root.xpath(
		'//*[@style[contains(.,"Symbol")] or '+
		'@face[contains(.,"Symbol")]]'
	):
		if not len(el):
			el.text=replobj["regex"].sub(
				lambda m: replobj["map"][re.escape(m.group(0))],
				el.text
			)

		if "face" in el.attrib:
			del el.attrib["face"]
		else:
			newstyle=re.sub(
				"(^|;).*?:\s*Symbol",
				'',
				el.get("style"),
				flags=re.S
			)
			if re.match("\s*", newstyle):
				del el.attrib["style"]
			else:
				el.set("style", newstyle)

	# замена meta тега, чтобы он соответствовал кодировке UTF-8
	for el in root.xpath("//meta"):
		el.getparent().remove(el)

	meta=lxml.etree.XML('<meta charset="UTF-8"/>')
	root.xpath("(//head)[1]")[0].insert(0, meta)

	root.write(fname, method='html', encoding='UTF-8', pretty_print=True)


# функция получает директорию из dir_conn,
# (или сигнал завершения), потом ждёт, пока завершится
# преобразование html в файлах этой директории —
# тогда их имена будут записаны в finish_conn,
# переименовывает файлы, потом директорию
def ioproc(finish_conn, dir_conn, num_threads):
	# в этом словаре хранятся соответствия:
	# имя директории — число файлов, которые ещё обрабатываются
	dirs={}
	working=True
	# число потоков, которые должны завершиться
	left_threads=num_threads
	while working:
		dirmsg=dir_conn.recv()
		#print("dirmsg", dirmsg)
		# сигнал завершения
		if "end" in dirmsg:
			working=False
			# пока не завершатся все потоки
			while left_threads!=0:
				finishmsg=finish_conn.recv()
				#print(" finishmsg", finishmsg)
				if "end" in finishmsg:
					left_threads-=1
				else:
					rename_to_lower(finishmsg["dir"],finishmsg["file"])
		else:
			# директория, которую нужно обработать
			fulldir=dirmsg["dir"]
			# возможно ранее завершённые файлы уже создали
			# запись в словаре
			if fulldir in dirs:
				num_left=dirs[fulldir]+dirmsg["filecount"]
			else:
				num_left=dirmsg["filecount"]

			# пока не закончат обрабатываться все файлы
			# в директории
			while num_left!=0:
				finishmsg=finish_conn.recv()
				#print(" finishmsg", finishmsg)
				# некоторые потоки могут завершиться
				if "end" in finishmsg:
					left_threads-=1
				else:
					rename_to_lower(finishmsg["dir"],finishmsg["file"])
					if finishmsg["dir"]==fulldir:
						num_left-=1
					else:
						# другая директория — уменьшить счётчики
						if not (finishmsg["dir"] in dirs):
							dirs[finishmsg["dir"]]=0
						dirs[finishmsg["dir"]]-=1

			#dirparts=os.path.split(fulldir)
			rename_to_lower(*os.path.split(fulldir))

# функция считывает имя файла из file_conn
# вызывает функцию для преобразования html и
# записыает в finish_conn имя файла
def htmlproc(
	finish_conn,
	file_conn,
	file_conn_lock,
	finish_conn_lock,
	replobj
):
	working=True
	while working:
		# нужно использовать мьютексы, ведь работают сразу несколько потоков
		with file_conn_lock:
			filemsg=file_conn.recv()
		#print("filemsg", filemsg)
		# сигнал завершения
		if "end" in filemsg:
			working=False
			with finish_conn_lock:
				finish_conn.send({"end":True})
		else:
			fname=filemsg["file"]
			dirname=filemsg["dir"]
			replace_to_lower(dirname, fname, replobj)

			with finish_conn_lock:
				finish_conn.send({"file":fname, "dir":dirname})

if len(sys.argv)>1:
	startdir=sys.argv[1]
	if not os.path.isdir(startdir):
		print("Не найдена директория "+startdir)
		sys.exit(1)
else:
	print("Укажите директорию!")
	sys.exit(1)

with open(os.path.join(
	os.path.dirname(os.path.realpath(__file__)),
	"symbol_map.json")
, "rt") as f:
	jsonmap=json.load(f)

# отображение символов из шрифта Symbol на юникод
jsonmap = dict((re.escape(k), v) for k, v in jsonmap.items())
replobj = {"regex":re.compile("|".join(jsonmap.keys())), "map":jsonmap}

num_threads=int(sys.argv[2] if len(sys.argv)>2 else cpu_count())

in_file_conn, out_file_conn = Pipe(False)
in_dir_conn, out_dir_conn = Pipe(False)
in_finish_conn, out_finish_conn = Pipe(False)

procs=[]

file_conn_lock=Lock()
finish_conn_lock=Lock()

# создание процессов, преобразующих html
for i in range(num_threads):
	p=Process(target=htmlproc, args=(
		out_finish_conn,
		in_file_conn,
		file_conn_lock,
		finish_conn_lock,
		replobj
	))
	p.start()
	procs.append(p)

# процесс, переименовающий файлы
p=Process(target=ioproc, args=(in_finish_conn,in_dir_conn,num_threads))
p.start()
procs.append(p)

# прохождение по всем файлам и директориям
for root, subdirs, files in os.walk(startdir, topdown=False):
	html_files=[]
	fin_files=[]
	for file in files:
		parts=os.path.splitext(file)
		ext=parts[1].lower()[1:]
		fname=parts[0].lower()

		htmlfile=(ext=="html" or ext=="htm")
		upperfile=(not file.islower())

		if htmlfile:
			html_files.append(file)

		if upperfile and not htmlfile:
			fin_files.append(file)

	# самую верхнюю директорию не нужно переименовывать
	if root==startdir:
		out_dir_conn.send({"end":True})
	else:
		out_dir_conn.send({
			"dir":root,
			"filecount":len(fin_files)+len(html_files)
		})

	# запись файлов в Pipe
	for file in html_files: out_file_conn.send({"file":file, "dir":root})
	for file in fin_files:
		out_finish_conn.send({"file":file, "dir":root})

for i in range(num_threads):
	out_file_conn.send({"end":True})

for p in procs:
	p.join()

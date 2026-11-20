# AED-Proyecto2-Diametro-Arbol

Implementación en **C++17** del diámetro de un árbol no dirigido y no ponderado mediante **dos BFS**, con exportación de los eventos reales a JSON `1.0` para animación en Manim u otra herramienta.

Proyecto Final 2 de Algoritmos y Estructuras de Datos, tema 12.

## Requisitos

- Compilador de C++17: g++ 9 o superior, clang o MSVC.
- CMake 3.16 o superior (opcional; también se puede compilar solo con g++).

## Compilar y ejecutar

Con CMake (desde la raíz del repositorio):

```bash
cd cpp
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
./build/diametro --input ../inputs/arbol_demo.txt --output ../outputs/animacion_diametro.json
```

Sin CMake, directamente con g++ (desde la raíz del repositorio):

```bash
g++ -std=c++17 -O2 -Icpp/include cpp/src/*.cpp -o diametro
./diametro --input inputs/arbol_demo.txt --output outputs/animacion_diametro.json
```

En Windows el ejecutable se llama `diametro.exe`. La opción `--start k` permite elegir el nodo desde el que empieza el primer BFS (por defecto, 1). Al terminar, el programa imprime algo como:

```text
n=7 | extremos=[7,4] | diametro=5 aristas
JSON generado: outputs/animacion_diametro.json
```

## ¿Cómo se resuelve el diámetro de un árbol?

El **diámetro** es el camino simple de mayor longitud entre dos nodos: un camino que no repite nodos. En este proyecto, cada arista vale una unidad, así que la longitud se mide contando aristas. No depende de cuál nodo dibujemos como raíz ni de la altura que tenga el dibujo del árbol.

En un árbol hay exactamente un camino simple entre cada par de nodos. Podríamos buscar la distancia entre todas las parejas, pero eso repetiría mucho trabajo. Una propiedad de los árboles no ponderados permite encontrar el diámetro con solo **dos búsquedas en anchura (BFS)**: un nodo a distancia máxima desde cualquier origen es un extremo de algún diámetro.

BFS explora por niveles mediante una cola FIFO: primero visita el origen, después sus vecinos a distancia `1`, luego los nodos a distancia `2`, y así sucesivamente. Al descubrir cada nodo, guarda su distancia y el padre desde el que llegó. Esto permite encontrar el nodo más lejano y reconstruir después el camino recorrido.

1. **Primer BFS:** partir de cualquier nodo `s` y encontrar el más lejano `a`. Este recorrido sirve para situarnos en un extremo del diámetro; su distancia desde `s` todavía puede ser menor que el diámetro.
2. **Segundo BFS:** partir de `a` y encontrar el más lejano `b`. Ahora la distancia entre `a` y `b` es la longitud del diámetro.
3. **Reconstruir el camino:** comenzar en `b`, seguir los padres guardados por el segundo BFS hasta llegar a `a` e invertir esa secuencia para entregar el camino de `a` hacia `b`.

En el ejemplo del proyecto, desde `1` el primer BFS alcanza `7` a distancia `4`. Al comenzar el segundo BFS en `7`, se alcanza `4` a distancia `5`. El diámetro es entonces **5 aristas** y su camino es **`7 → 6 → 5 → 2 → 3 → 4`**. Si hay varios nodos igualmente lejanos, elegimos el de menor ID para obtener siempre el mismo resultado.

Cada BFS visita cada nodo una vez y examina cada arista en sus dos sentidos. Como un árbol de `n` nodos tiene `n-1` aristas, la solución completa requiere **O(n) tiempo y O(n) memoria**. Para un árbol de un solo nodo, ambos extremos son ese mismo nodo, el camino contiene solo ese nodo y el diámetro es `0`.

## Entrada

```text
7
1 2
2 3
3 4
2 5
5 6
6 7
```

Cada línea no vacía contiene un registro: primero `n >= 1`, después exactamente `n-1` pares. Se ignoran líneas vacías, espacios, tabulaciones y finales LF/CRLF. Se rechazan comentarios, pesos, tokens adicionales, pares partidos entre líneas, IDs fuera de `1..n`, lazos, aristas duplicadas en cualquiera de sus orientaciones y grafos desconectados. Los enteros deben estar dentro de `[-2147483647, 2147483647]`; el número de nodos se limita a `INT_MAX/2` por los índices de las dos orientaciones, y al espacio disponible en memoria.

## Estructuras implementadas manualmente

- `Array<T>`: arreglo de tamaño fijo con `new[]`/`delete[]`, propiedad exclusiva y movimiento; libera memoria al salir de ámbito incluso ante errores.
- `Tree`: listas de adyacencia enlazadas en arreglos propios, con cabeza y cola por nodo. Insertar al final conserva el orden del archivo en tiempo constante.
- `Queue`: cola circular FIFO con arreglo propio, sin `std::queue`.
- `Trace`: arreglo propio de eventos tipados; registra cada operación durante la ejecución, sin guardar copias completas del estado.
- BFS, distancias, padres, desempate por menor ID y reconstrucción del camino: lógica propia e iterativa.
- Serialización JSON manual con funciones de E/S estándar; los textos serializados son constantes ASCII y los valores variables son enteros. Las rutas nunca se incluyen en el JSON.

No se usan contenedores ni algoritmos STL (`vector`, `queue`, `list`, `map`, `set`, `sort`, etc.) ni bibliotecas JSON externas. Se usa la biblioteca estándar para E/S, excepciones y `std::filesystem::equivalent`, que evita sobrescribir la entrada mediante alias de ruta. En Windows se usa además `MoveFileExA` para reemplazar la salida terminada. Las rutas de la CLI utilizan la codificación nativa de las funciones de archivos de C; para máxima portabilidad en Windows, usar nombres de ruta ASCII. El contenido JSON siempre es ASCII y, por tanto, UTF-8 válido.

## Algoritmo y costo

1. Leer las aristas y construir ambas orientaciones de la adyacencia.
2. Detectar duplicados mediante marcas por origen: al recorrer los vecinos de `u`, marcar cada vecino con `u`. Una marca repetida identifica una arista duplicada. No requiere ordenar, hashing ni comparar cada arista con todas las anteriores.
3. Verificar conectividad con una cola propia. Este recorrido de validación no forma parte de las dos fases animadas.
4. BFS 1 desde el nodo inicial: encontrar el nodo más lejano `a`.
5. BFS 2 desde `a`: encontrar el nodo más lejano `b`; su distancia es el diámetro.
6. Seguir los padres del segundo BFS desde `b` y llenar el camino en orden inverso, entregándolo desde `a` hasta `b`.

En un árbol existe un único camino entre cada par de nodos; el primer recorrido alcanza un extremo de algún diámetro y el segundo obtiene su longitud máxima. Los empates se resuelven comparando IDs durante el recorrido, sin alterar el orden de vecinos.

Cada BFS descubre, encola, desencola y termina cada nodo una vez; examina cada arista dos veces. Emite `2 + 4n + 2(n-1) = 6n` eventos. Ambos BFS y los dos eventos de resultado suman **`12n + 2`**. Esta capacidad se reserva una sola vez y el contador refleja las inserciones reales.

La lectura estructural, validación, ambos BFS, comprobación del camino y registro de eventos requieren **O(n) tiempo y O(n) memoria**, en el modelo usual de operaciones sobre enteros. El JSON contiene O(n) registros; su tamaño en bytes también incluye la cantidad de dígitos de los IDs y distancias, y el costo de lectura/escritura es proporcional al texto procesado. No se usa recursión ni snapshots de la cola.

## Entrega al animador

Leer `schema_version == "1.0"` y reproducir `events` en orden de `step`:

| Evento | Acción |
|---|---|
| `phase_start` | Reiniciar cola y estilos de visita; mantener el árbol. |
| `node_discovered` | Mostrar distancia y padre; marcar descubierto. |
| `node_enqueued` / `node_dequeued` | Añadir al final / retirar del frente de la cola visual. |
| `edge_examined` | Iluminar `edge_id`; distinguir `new` y `already_seen`. |
| `node_finished` | Marcar el nodo como terminado. |
| `phase_end` | Resaltar `farthest_node` y su distancia. |
| `diameter_path` | Resaltar `nodes` y `edges` en el orden recibido. |
| `diameter_result` | Mostrar extremos y longitud final. |

El animador decide coordenadas y duración de cada transición. No necesita recalcular recorridos ni distancias. `runs` conserva los resultados de ambos BFS ordenados por ID y `diameter` incluye el camino final. Los IDs de arista siempre son `min-max`, incluso cuando se recorren en el sentido opuesto.

## Integridad de salida

Primero se valida toda la entrada y el resultado. Se escribe un temporal exclusivo junto al destino; se comprueban escritura, vaciado y cierre antes de renombrarlo. Ante fallos se limpia el temporal propio y se conserva la salida anterior. Un temporal preexistente no se sobrescribe. No se imprime éxito si el reemplazo falla. Una interrupción abrupta puede dejar un temporal que se puede eliminar manualmente; nunca se presenta como el JSON final.

## Validación realizada

La implementación se verificó localmente con 23 pruebas, que cubren los casos normales y borde, las entradas inválidas, la traza JSON completa, 75 árboles aleatorios y una cadena y una estrella de 10 000 nodos. Las pruebas y la configuración de CI se conservan localmente y están excluidas del seguimiento de Git. El repositorio distribuido contiene el código necesario para generar los JSON, las entradas de ejemplo y la documentación.

## Archivos

```text
cpp/include/    Arreglos, cola, árbol, eventos y resultados
cpp/src/        Lectura, validación, BFS, diámetro, JSON y CLI
inputs/         Árbol demo, un nodo, dos nodos, cadena y estrella
outputs/        JSON completos generados por C++ para animación
```

class RunwayRenderer{

    constructor(map){

        this.map=map;

    }

    metersToDegrees(lat, metersX, metersY){

        const dLat = metersY / 111320;

        const dLon = metersX / (111320 * Math.cos(lat * Math.PI / 180));

        return [

            dLon,

            dLat

        ];

    }

  runwayPolygon(runway){

    const coords = runway.geometry;

    const width = Number(runway.width) / 2;

    const R = 6378137;

    const left = [];
    const right = [];

    for(let i=0;i<coords.length;i++){

        const p = coords[i];

        let prev = coords[Math.max(i-1,0)];
        let next = coords[Math.min(i+1,coords.length-1)];

        const lat0 = p[1] * Math.PI / 180;

        const x1 = prev[0] * Math.PI / 180 * R * Math.cos(lat0);
        const y1 = prev[1] * Math.PI / 180 * R;

        const x2 = next[0] * Math.PI / 180 * R * Math.cos(lat0);
        const y2 = next[1] * Math.PI / 180 * R;

        const dx = x2 - x1;
        const dy = y2 - y1;

        const len = Math.sqrt(dx*dx + dy*dy);

        const nx = -dy / len;
        const ny = dx / len;

        const x = p[0] * Math.PI / 180 * R * Math.cos(lat0);
        const y = p[1] * Math.PI / 180 * R;

        function ll(px,py){

            return [

                px / (R*Math.cos(lat0)) * 180 / Math.PI,

                py / R * 180 / Math.PI

            ];

        }

        left.push(

            ll(

                x + nx*width,

                y + ny*width

            )

        );

        right.unshift(

            ll(

                x - nx*width,

                y - ny*width

            )

        );

    }

    return{

        type:"Feature",

        properties:{

            name:runway.ref

        },

        geometry:{

            type:"Polygon",

            coordinates:[[

                ...left,

                ...right,

                left[0]

            ]]

        }

    };

}
drawCenterlines(runways){

    const features=[];

    runways.forEach(runway=>{

        const coords=runway.geometry;

        if(coords.length<2)return;

        const width=1.0;

        const stripe=30;

        const gap=20;

        const R=6378137;

        for(let i=0;i<coords.length-1;i++){

            const a=coords[i];
            const b=coords[i+1];

            const lat0=((a[1]+b[1])/2)*Math.PI/180;

            const x1=a[0]*Math.PI/180*R*Math.cos(lat0);
            const y1=a[1]*Math.PI/180*R;

            const x2=b[0]*Math.PI/180*R*Math.cos(lat0);
            const y2=b[1]*Math.PI/180*R;

            const dx=x2-x1;
            const dy=y2-y1;

            const len=Math.sqrt(dx*dx+dy*dy);

            const tx=dx/len;
            const ty=dy/len;

            const nx=-ty;
            const ny=tx;

            let d=60;

            while(d+stripe<len-60){

                const sx=x1+tx*d;
                const sy=y1+ty*d;

                const ex=x1+tx*(d+stripe);
                const ey=y1+ty*(d+stripe);

                const p1=[sx+nx*width,sy+ny*width];
                const p2=[sx-nx*width,sy-ny*width];
                const p3=[ex-nx*width,ey-ny*width];
                const p4=[ex+nx*width,ey+ny*width];

                function ll(x,y){

                    return[
                        x/(R*Math.cos(lat0))*180/Math.PI,
                        y/R*180/Math.PI
                    ];

                }

                features.push({

                    type:"Feature",

                    geometry:{

                        type:"Polygon",

                        coordinates:[[

                            ll(...p1),
                            ll(...p2),
                            ll(...p3),
                            ll(...p4),
                            ll(...p1)

                        ]]

                    }

                });

                d+=stripe+gap;

            }

        }

    });

    if(this.map.getLayer("runway-centerline")){

        this.map.removeLayer("runway-centerline");

    }

    if(this.map.getSource("runway-centerline")){

        this.map.removeSource("runway-centerline");

    }

    this.map.addSource("runway-centerline",{

        type:"geojson",

        data:{

            type:"FeatureCollection",

            features

        }

    });

    this.map.addLayer({

        id:"runway-centerline",

        type:"fill",

        source:"runway-centerline",

        paint:{

            "fill-color":"white"

        }

    });

}
   draw(runways){

    const polygons=[];

    runways.forEach(r=>{

        polygons.push(
            this.runwayPolygon(r)
        );

    });

    if(this.map.getLayer("runway-outline")){

        this.map.removeLayer("runway-outline");

    }

    if(this.map.getLayer("runway-surface")){

        this.map.removeLayer("runway-surface");

    }

    if(this.map.getSource("runway-surface")){

        this.map.removeSource("runway-surface");

    }

    this.map.addSource("runway-surface",{

        type:"geojson",

        data:{

            type:"FeatureCollection",

            features:polygons

        }

    });

    this.map.addLayer({

        id:"runway-surface",

        type:"fill",

        source:"runway-surface",

        paint:{

            "fill-color":"#2e2e2e",

            "fill-outline-color":"#8a8a8a",

            "fill-opacity":1

        }

    });


    this.map.addLayer({

        id:"runway-outline",

        type:"line",

        source:"runway-surface",

        paint:{

            "line-color":"#bdbdbd",

            "line-width":1.5

        }

    });
this.drawCenterlines(runways);
}}